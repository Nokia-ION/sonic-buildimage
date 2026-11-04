// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	ownershipvoucher "github.com/openconfig/bootz/common/ownership_voucher"
	"github.com/openconfig/bootz/common/signature"
	bpb "github.com/openconfig/bootz/proto/bootz"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// verifyVoucher ensures the OV is valid and returns the Pinned Domain Cert (PDC).
func (bc *BootzContext) verifyVoucher(ovRaw []byte,
	cardSerialNo, chassisSerialNo string) (*x509.Certificate, error) {

	manufPool, err := LoadManufCA(bc.config)
	if err != nil {
		return nil, err
	}

	// verify the voucher has been signed by manuf cert
	parsedOV, err := ownershipvoucher.Unmarshal(ovRaw, manufPool)
	if err != nil {
		return nil, fmt.Errorf("ownership voucher signature verification failed: %w", err)
	}
	slog.Info("validated ownership-voucher signed by manufacturer")

	// verify the serial number is correct
	ovSerial := parsedOV.OV.SerialNumber
	if ovSerial != cardSerialNo && ovSerial != chassisSerialNo {
		return nil, fmt.Errorf("serial mismatch: OV has %s, but expected either card (%s) or chassis (%s)",
			ovSerial, cardSerialNo, chassisSerialNo)
	}
	slog.Info("ownership voucher serial number validated", "matched_serial", ovSerial)

	// Parse Voucher's PDC (Pinned Domain Cert)
	pdc, err := x509.ParseCertificate(parsedOV.OV.PinnedDomainCert)
	if err != nil {
		return nil, fmt.Errorf("failed to parse PDC: %w", err)
	}

	slog.Info("voucher PDC details",
		"subject", pdc.Subject.String(),
		"issuer", pdc.Issuer.String(),
	)

	return pdc, nil
}

// verifyResponseSignature checks the signature on the actual bootstrap data.
func (bc *BootzContext) verifyResponseSignature(
	ocCert *x509.Certificate, resp *bpb.GetBootstrapDataResponse) error {
	data := resp.GetSerializedBootstrapData()
	sig := resp.GetResponseSignature()

	// Validate the response signature using the verified OC
	if err := signature.Verify(ocCert, data, sig); err != nil {
		return fmt.Errorf("response signature invalid: %w", err)
	}

	slog.Info("validated response signed by ownership-certificate")
	return nil
}

// validateArtifacts orchestrates the full security handshake for the GetBootstrapDataResponse.
// It performs the following security validations:
//
//   - Verifies the Ownership Voucher (OV) is signed by the manufacturer.
//   - Ensures the serial number in the OV matches the requested serialNumber.
//   - Confirms the Ownership Certificate is part of the Pinned Domain Cert chain.
//
// If any validation fails, return a descriptive error.
func (bc *BootzContext) validateArtifacts(
	cardSerialNo,
	chassisSerialNo string,
	resp *bpb.GetBootstrapDataResponse) error {

	// Validate the Ownership Voucher (OV)
	pdc, err := bc.verifyVoucher(resp.GetOwnershipVoucher(), cardSerialNo, chassisSerialNo)
	if err != nil {
		bc.ReportFailureStatus("Ownership voucher verification failed")
		return err
	}
	bc.ReportInfoStatus("Ownership voucher verified")

	// Extract and Verify the Ownership Certificate (OC) from the degenerate CMS
	ocCert, err := bc.extractAndVerifyOC(resp.GetOwnershipCertificate(), pdc)
	if err != nil {
		bc.ReportFailureStatus("Ownership certificate verification failed")
		return err
	}
	bc.ReportInfoStatus("Ownership certificate verified")

	// Verify the final payload signature
	res := bc.verifyResponseSignature(ocCert, resp)
	bc.ReportInfoStatus("Response signature verified")
	return res
}

// writeBootstrapJsonData writes entire BootstrapDataResponse to a JSON file
func (bc *BootzContext) writeBootstrapJsonData(resp *bpb.BootstrapDataResponse, filename string) error {
	// Marshal the proto message to JSON bytes
	// Use protojson.MarshalOptions to customize output
	options := protojson.MarshalOptions{
		Multiline:       true,  // Pretty-print
		Indent:          "  ",  // Two-space indent
		EmitUnpopulated: false, // Omit empty fields
		UseProtoNames:   true,  // Matches preserve_proto_field_names
		UseEnumNumbers:  true,  // Matches always_print_enums_as_ints
	}

	jsonData, err := options.Marshal(resp)
	if err != nil {
		slog.Error("failed to marshal bootstrap data to JSON", "error", err)
		return err
	}

	// The bootstrap data can carry certz private keys.
	if err := os.WriteFile(filename, jsonData, 0600); err != nil {
		return err
	}
	bc.ReportInfoStatus("Wrote bootstrap data to file %s", filename)
	return nil
}

// runConfigInstaller if an installer is specified invoke it providing
// it with the path to the staged vendor config file
func runConfigInstaller(installerPath, configFilePath string) error {
	slog.Info("running config installer", "path", installerPath, "config-file", configFilePath)

	// Execute the installer: path/to/installer <vendor-config-file>
	cmd := exec.Command(installerPath, configFilePath)

	// Capture output for logging/debugging
	output, err := cmd.CombinedOutput()
	if err != nil {
		slog.Error("config installer failed", "error", err, "output", string(output))
		return fmt.Errorf("config installer: %w (output: %s)", err, output)

	}

	slog.Info("config installer completed successfully", "output", string(output))
	return nil
}

// processBootstrapConfig extracts and executes the bootstrap instructions
// from a signed response.
//
// This function only processes the first Bootstrap Config entry
// found in the response, handling image downloading, hash validation,
// and vendor configuration installation. If modular device, the config
// will be synched to the standby supervisor.
func (bc *BootzContext) processBootstrapConfig(
	ctx context.Context,
	cfg *Config,
	signedResp *bpb.BootstrapDataSigned) error {
	slog.Info("processing bootstrap config")

	responses := signedResp.GetResponses()
	if len(responses) == 0 {
		return fmt.Errorf("signed response contained no bootstrap responses")
	}

	// Warning if multiple configs are present, only first will be used
	if len(responses) > 1 {
		slog.Warn("multiple control card configs provided",
			"count", len(responses),
			"limit", 1)
	}

	// Just use the first response
	data := responses[0]
	slog.Info("received bootstrap config")

	// optionally save entire bootstrap data as json
	if cfg.BootstrapJsonFile != "" {
		if err := bc.writeBootstrapJsonData(data, cfg.BootstrapJsonFile); err != nil {
			return fmt.Errorf("failed to write bootstrap-data to %s: %w",
				cfg.BootstrapJsonFile, err)
		}

		slog.Info("successfully wrote bootstrap-data(JSON)",
			"filename", cfg.BootstrapJsonFile)
	}

	// Staged before the image step, which may reboot into an image that
	// installs them on first boot.
	certzProfiles, err := bc.stageCertzProfiles(data)
	if err != nil {
		return fmt.Errorf("failed to process certz profiles: %w", err)
	}
	if certzProfiles > 0 {
		bc.ReportInfoStatus("Staged %d certz profile(s)", certzProfiles)
	}

	// Validated before the image step, which stages it for the new image.
	bc.vendorConfig = data.GetBootConfig().GetVendorConfig()
	if err := validateVendorConfig(bc.vendorConfig); err != nil {
		return err
	}

	// Check if the IntendedImage message exists
	intendedImage := data.GetIntendedImage()
	if intendedImage == nil {
		slog.Warn("intended image not provided. skipping SWI install.")
	} else {
		if cfg.Verbose {
			LogBanner("Processing Intended Image")
		}
		if err := bc.ProcessIntendedImage(ctx, cfg, intendedImage); err != nil {
			return fmt.Errorf("failed to process intended image: %w", err)
		}
		if cfg.Verbose {
			LogBanner("Intended Image Complete")
		}
	}

	if certzProfiles > 0 {
		if bc.imageInstalled && cfg.ImageFile == "" {
			slog.Info("certz profiles will be installed by the new image on first boot",
				"path", PendingCertzDir)
		} else {
			if err := InstallCertzProfiles(); err != nil {
				return fmt.Errorf("failed to install certz profiles: %w", err)
			}
			bc.ReportInfoStatus("Installed %d certz profile(s)", certzProfiles)
		}
	}

	// Vendor config is only attribute of interest (for now)
	config := bc.vendorConfig
	configLen := len(config)
	limit := 64

	if configLen == 0 {
		slog.Warn("vendor config is empty; skipping file write.")
	} else if bc.imageInstalled && cfg.ImageFile == "" {
		slog.Info("vendor config will be merged by the new image on first boot",
			"path", bc.stagedVendorConfig)
	} else {
		content := string(config)
		isTruncated := false

		if configLen > limit {
			content = string(config[:limit]) + "..."
			isTruncated = true
		}

		slog.Info("staging vendor config for the running image",
			"size_bytes", configLen,
			"truncated", isTruncated,
			"content", content,
		)

		if err := bc.stageRunningImageVendorConfig(); err != nil {
			return err
		}

		if cfg.ConfigInstaller != "" {
			if err := runConfigInstaller(cfg.ConfigInstaller, bc.stagedVendorConfig); err != nil {
				return err // This will trigger a retry in ExecuteWithRetry
			}
		} else {
			slog.Info("skipping config installed, no config installer configured")
		}
	}

	slog.Info("completed processing bootstrap config")
	return nil
}

const (
	// VendorConfigFile is where an image finds the vendor config staged for
	// it, relative to its /host/image-<version> directory. The config-setup
	// hook 90-bootz-vendor-config reads the same path.
	VendorConfigFile = "bootz/vendor_config.json"

	// migratedConfigDir is where sonic-installer backs up /etc/sonic for the
	// new image to migrate.
	migratedConfigDir = "/host/old_config"

	// startupConfigBackupSuffix is appended to the running image's startup
	// configs when they are moved aside.
	startupConfigBackupSuffix = ".pre-bootz"
)

// startupConfigFiles are the startup configs that config-setup uses instead
// of generating the factory default config.
var startupConfigFiles = []string{"config_db.json", "minigraph.xml"}

// validateVendorConfig checks that a non-empty vendor config is a
// config_db.json object.
func validateVendorConfig(config []byte) error {
	if len(config) == 0 {
		return nil
	}
	var tables map[string]json.RawMessage
	if err := json.Unmarshal(config, &tables); err != nil {
		return fmt.Errorf("vendor config is not a valid config_db.json object: %w", err)
	}
	if tables == nil {
		return fmt.Errorf("vendor config is not a valid config_db.json object: got null")
	}
	return nil
}

// imageDir returns the /host directory of the SONiC image with the given
// build version.
func imageDir(version string) (string, error) {
	dir := filepath.Join("/host", "image-"+version)
	fi, err := os.Stat(dir)
	if err != nil {
		return "", fmt.Errorf("image directory of %q: %w", version, err)
	}
	if !fi.IsDir() {
		return "", fmt.Errorf("image directory of %q: %s is not a directory", version, dir)
	}
	return dir, nil
}

// installedImageDir returns the /host directory of the image installed from
// the installer file localPath.
func installedImageDir(localPath string) (string, error) {
	out, err := exec.Command("sudo", "sonic-installer", "binary-version", localPath).Output()
	if err != nil {
		return "", fmt.Errorf("sonic-installer binary-version %s: %w (output: %s)",
			localPath, err, strings.TrimSpace(string(out)))
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	version, ok := strings.CutPrefix(strings.TrimSpace(lines[len(lines)-1]), "SONiC-OS-")
	if !ok || version == "" {
		return "", fmt.Errorf("unexpected sonic-installer binary-version output %q", out)
	}
	return imageDir(version)
}

// stageVendorConfig writes the vendor config into the image directory dir.
func (bc *BootzContext) stageVendorConfig(dir string) error {
	path := filepath.Join(dir, VendorConfigFile)
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return fmt.Errorf("create %s: %w", filepath.Dir(path), err)
	}
	if err := writeFileAtomic(path, bc.vendorConfig, 0644); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	bc.stagedVendorConfig = path
	slog.Info("staged vendor config", "path", path)
	return nil
}

// stageNewImageVendorConfig stages the vendor config for the image installed
// from localPath. The image boots without a startup config, so config-setup
// generates the factory default config and merges the vendor config into it.
func (bc *BootzContext) stageNewImageVendorConfig(localPath string) error {
	if len(bc.vendorConfig) == 0 {
		return nil
	}
	dir, err := installedImageDir(localPath)
	if err != nil {
		return err
	}
	if err := bc.stageVendorConfig(dir); err != nil {
		return err
	}
	for _, name := range startupConfigFiles {
		path := filepath.Join(migratedConfigDir, name)
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("remove %s: %w", path, err)
		}
	}
	return nil
}

// stageRunningImageVendorConfig stages the vendor config for the running
// image and moves its startup configs aside, so on the next boot config-setup
// generates the factory default config and merges the vendor config into it,
// as it does for a new image.
func (bc *BootzContext) stageRunningImageVendorConfig() error {
	dir, err := imageDir(ReadCurrentVersion())
	if err != nil {
		return err
	}
	if err := bc.stageVendorConfig(dir); err != nil {
		return err
	}
	for _, name := range startupConfigFiles {
		path := filepath.Join("/etc/sonic", name)
		err := os.Rename(path, path+startupConfigBackupSuffix)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return fmt.Errorf("move %s aside: %w", path, err)
		}
		bc.movedStartupConfigs = append(bc.movedStartupConfigs, path)
		slog.Info("moved startup config aside", "path", path, "backup", path+startupConfigBackupSuffix)
	}
	return nil
}

// unstageVendorConfig undoes the staging of a failed provisioning run, which
// is followed by a reboot.
func (bc *BootzContext) unstageVendorConfig() {
	if bc.stagedVendorConfig != "" {
		if err := os.Remove(bc.stagedVendorConfig); err != nil && !os.IsNotExist(err) {
			slog.Warn("failed to remove staged vendor config", "path", bc.stagedVendorConfig, "err", err)
		}
		bc.stagedVendorConfig = ""
	}
	for _, path := range bc.movedStartupConfigs {
		if err := os.Rename(path+startupConfigBackupSuffix, path); err != nil {
			slog.Warn("failed to restore startup config", "path", path, "err", err)
		}
	}
	bc.movedStartupConfigs = nil
}

// PendingTelemetryDir is where the new image's first boot (config-setup
// do_config_migration) restores /etc/sonic/telemetry from.
const PendingTelemetryDir = "/host/old_config/telemetry"

// stageTelemetryDir carries the gNMI certificate directory over to the new
// image. The Bootz certz profiles are installed after the migration, so they
// replace carried-over files of the same name.
func stageTelemetryDir() error {
	fi, err := os.Stat(telemetryCertDir)
	if os.IsNotExist(err) {
		slog.Info("no telemetry certificates to carry over", "path", telemetryCertDir)
		return nil
	}
	if err != nil {
		return err
	}
	if !fi.IsDir() {
		return fmt.Errorf("%s is not a directory", telemetryCertDir)
	}
	if err := os.RemoveAll(PendingTelemetryDir); err != nil {
		return fmt.Errorf("remove %s: %w", PendingTelemetryDir, err)
	}
	if err := os.MkdirAll(PendingTelemetryDir, fi.Mode().Perm()); err != nil {
		return fmt.Errorf("create %s: %w", PendingTelemetryDir, err)
	}
	// cp -a keeps the key file modes and the Certz symlinks.
	out, err := exec.Command("cp", "-a", telemetryCertDir+"/.", PendingTelemetryDir).CombinedOutput()
	if err != nil {
		os.RemoveAll(PendingTelemetryDir)
		return fmt.Errorf("copy %s to %s: %w (output: %s)", telemetryCertDir, PendingTelemetryDir, err, out)
	}
	slog.Info("staged telemetry certificates for the new image", "path", PendingTelemetryDir)
	return nil
}

// buildBootstrapRequest constructs the GetBootstrapDataRequest message.
// It bundles the chassis-specific hardware identity, the current state of the
// active control card, and a cryptographically random nonce.
func buildBootstrapRequest(
	chassis *bpb.ChassisDescriptor,
	card *bpb.ControlCardState,
	nonce string) *bpb.GetBootstrapDataRequest {
	req := &bpb.GetBootstrapDataRequest{
		ChassisDescriptor: chassis,
		ControlCardState:  card,
		Nonce:             nonce,
	}
	slog.Info("built bootstrap data request",
		"manufacturer", req.ChassisDescriptor.Manufacturer,
		"chassis_sn", req.ChassisDescriptor.SerialNumber,
		"card_sn", req.ControlCardState.SerialNumber,
		"status", req.ControlCardState.Status,
		"nonce_len", len(req.Nonce),
	)
	return req
}

// handleResponse orchestrates the end-to-end processing of the bootstrap server's response.
// It performs the following sequential operations:
//   - Security Validation: Verifies the Ownership Voucher (OV), Ownership Certificate (OC),
//     and the server's signature against the trusted vendor CA.
//   - Integrity Check: Unmarshals the signed bootstrap data and validates the nonce to
//     prevent replay attacks.
//   - Configuration Execution: Hands off the validated configuration data to be applied
//     to the system (e.g., SWI installation, config_db updates).
//   - Final Reporting: Sends a terminal status report (SUCCESS/FAILURE) back to the
//     Bootz server via the gRPC stream.
//
// It returns an error if any step fails, allowing the agent to fail-over to the next server.
func (bc *BootzContext) handleResponse(ctx context.Context,
	cfg *Config,
	resp *bpb.GetBootstrapDataResponse,
	cardSerialNo, chassisSerialNo, nonce string) error {
	LogBanner("Processing Bootstrap Artifacts")
	bc.ReportInfoStatus("Processing bootstrap artifacts")

	// Security Validation
	if !cfg.InsecureBoot {
		if err := bc.validateArtifacts(cardSerialNo, chassisSerialNo, resp); err != nil {
			return fmt.Errorf("security validation failed: %w", err)
		}
	}

	// Unmarshal Signed Content
	var signedResp bpb.BootstrapDataSigned
	if err := proto.Unmarshal(resp.GetSerializedBootstrapData(), &signedResp); err != nil {
		return fmt.Errorf("failed to unmarshal bootstrap data: %w", err)
	}

	// Nonce Check (Anti-Replay)
	if !cfg.InsecureBoot && signedResp.GetNonce() != nonce {
		return fmt.Errorf("nonce mismatch (expected %q, got %q)",
			nonce, signedResp.GetNonce())
	}

	// Apply Configuration (Config + Image)
	if err := bc.processBootstrapConfig(ctx, cfg, &signedResp); err != nil {
		bc.ReportFailureStatus(fmt.Sprintf("Failed to process bootstrap config: %v", err))
		// Without this, the next boot would report SUCCESS for a failed run.
		if rmErr := os.Remove(PendingReportFile); rmErr != nil && !os.IsNotExist(rmErr) {
			slog.Warn("failed to remove pending status report", "err", rmErr)
		}
		if rmErr := os.RemoveAll(PendingCertzDir); rmErr != nil {
			slog.Warn("failed to remove staged certz profiles", "err", rmErr)
		}
		bc.unstageVendorConfig()
		return fmt.Errorf("failed to process bootstrap config: %w", err)
	}

	// Final Report
	// If this fails, return an error so the loop knows provisioning wasn't
	// technically "confirmed" by the server.
	if cfg.SendFinalReport {
		if err := bc.ReportFinalStatus(); err != nil {
			return fmt.Errorf("failed to send final status: %w", err)
		}
	} else {
		bc.ReportInfoStatus("Completed processing all bootstrap data")
		// Provision mode reboots on success, so the SUCCESS report has to
		// come from the next boot, as it does after an image install.
		if !bc.imageInstalled && cfg.ImageFile == "" && !cfg.SkipReboot {
			if err := savePendingReport(cfg, bc.chassis); err != nil {
				slog.Error("failed to save pending status report", "err", err)
				bc.ReportInfoStatus("Final status will not be reported after reboot: %v", err)
			}
		}
	}
	return nil
}

// getBootstrapDataStream handles the streaming bootstrap workflow.
func getBootstrapDataStream(
	ctx context.Context,
	cfg *Config,
	c bpb.BootstrapClient,
	req *bpb.GetBootstrapDataRequest,
) (*bpb.GetBootstrapDataResponse, bpb.Bootstrap_BootstrapStreamClient, error) {

	// Resolve the IDevID using either simulated IDevId certs or actual
	idevidPriv, idevidPub, err := ResolveIDevID(cfg.IdevidCertFile, cfg.IdevidKeyFile)
	if err != nil {
		return nil, nil, fmt.Errorf("resolve idevid: %w", err)
	}

	// Prepare the Identity
	req.Identity = &bpb.Identity{
		Type: &bpb.Identity_IdevidCert{IdevidCert: string(idevidPub)},
	}

	// Construct the wrapper Bootstrap message
	// Note. Only Bootz v0.6 is currently supported.
	initialReq := &bpb.BootstrapStreamRequest{
		Type: &bpb.BootstrapStreamRequest_BootstrapRequest{
			BootstrapRequest: req,
		},
	}

	// Authenticate (Identity -> Challenge -> Response)
	stream, err := AuthenticateStream(ctx, cfg, c, idevidPriv, initialReq)
	if err != nil {
		return nil, nil, err
	}

	// Receive the actual Bootstrap Data
	slog.Info("waiting for bootstrap data response")
	bootstrapDataResp, err := stream.Recv()
	if err != nil {
		return nil, nil, fmt.Errorf("failed to receive bootstrap data: %w", err)
	}

	bootstrapResp := bootstrapDataResp.GetBootstrapResponse()

	return bootstrapResp, stream, nil
}

// runProvisioning encapsulates the end-to-end gRPC bootstrapping logic for a
// single server attempt. Using the chassis and controlCard state, it constructs
// a BootstrapRequest, initiates the bidirectional data stream, and handles
// the server's challenge-response exchange.
//
// It returns an error if the stream cannot be established or if the
// response processing fails, allowing the caller to failover to
// subsequent addresses in the pool.
func runProvisioning(ctx context.Context,
	cfg *Config,
	conn *grpc.ClientConn,
	chassis *bpb.ChassisDescriptor,
	controlCard *bpb.ControlCardState) error {
	client := bpb.NewBootstrapClient(conn)

	// build BootstrapRequest
	nonce, err := GenerateNonce()
	if err != nil {
		return fmt.Errorf("nonce generation failed: %w", err)
	}
	req := buildBootstrapRequest(chassis, controlCard, nonce)

	resp, stream, err := getBootstrapDataStream(ctx, cfg, client, req)
	if err != nil {
		return fmt.Errorf("getBootstrapDataStream failed: %w", err)
	}

	// Process and Execute
	bc := &BootzContext{stream: stream, chassis: chassis, config: cfg}
	if err := bc.handleResponse(
		ctx, cfg, resp,
		controlCard.GetSerialNumber(),
		chassis.GetSerialNumber(),
		nonce); err != nil {
		return err
	}

	// Graceful Close: Tell the server client is done sending flushing any pending status reports
	if err := stream.CloseSend(); err != nil {
		slog.Warn("failed to close stream", "err", err)
	}

	// Wait for Server EOF to ensure the server received the final status report
	_, err = stream.Recv()
	if err != io.EOF {
		slog.Warn("unexpected response", "expected", "eof", "err", err)
	}

	return nil
}

// AttemptProvisioning attempts to connect and provision using a bootz-server
func AttemptProvisioning(ctx context.Context,
	cfg *Config,
	addr string,
	chassis *bpb.ChassisDescriptor,
	card *bpb.ControlCardState) error {
	conn, err := ConnectToBootz(cfg, addr)
	if err != nil {
		return err
	}
	defer conn.Close() // Ensures closure even if runProvisioning panics

	return runProvisioning(ctx, cfg, conn, chassis, card)
}
