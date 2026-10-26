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
// it with the path to the config-db file
func runConfigInstaller(installerPath, configFilePath string) error {
	slog.Info("running config installer", "path", installerPath, "config-file", configFilePath)

	// Execute the installer: path/to/installer <config-db-file>
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
		slog.Info("vendor config will be applied by the new image on first boot",
			"path", PendingConfigDBFile)
	} else {
		content := string(config)
		isTruncated := false

		if configLen > limit {
			content = string(config[:limit]) + "..."
			isTruncated = true
		}

		slog.Info("installing vendor config",
			"size_bytes", configLen,
			"truncated", isTruncated,
			"content", content,
		)

		// Write the contents to the file specified by the flag
		// 0644 sets permissions to: Owner (Read/Write), Group (Read), Others (Read)
		if err := os.WriteFile(cfg.ConfigDBFile, config, 0644); err != nil {
			return fmt.Errorf("failed to write vendor config to %s: %w",
				cfg.ConfigDBFile, err)
		}
		slog.Info("successfully wrote vendor config", "filename", cfg.ConfigDBFile)

		if cfg.ConfigInstaller != "" {
			if err := runConfigInstaller(cfg.ConfigInstaller, cfg.ConfigDBFile); err != nil {
				return err // This will trigger a retry in ExecuteWithRetry
			}
		} else {
			slog.Info("skipping config installed, no config installer configured")
		}
	}

	slog.Info("completed processing bootstrap config")
	return nil
}

// PendingConfigDBFile is the startup config handed to a newly installed image.
const PendingConfigDBFile = "/host/old_config/config_db.json"

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
	if _, ok := tables["DEVICE_METADATA"]; !ok {
		slog.Warn("vendor config has no DEVICE_METADATA table; it replaces the whole startup config")
	}
	return nil
}

// stageVendorConfig places the vendor config where the new image's first
// boot migrates it into /etc/sonic/config_db.json (rc.local moves
// /host/old_config to /etc/sonic/old_config and config-setup loads it).
func stageVendorConfig(config []byte) error {
	if len(config) == 0 {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(PendingConfigDBFile), 0755); err != nil {
		return fmt.Errorf("create %s: %w", filepath.Dir(PendingConfigDBFile), err)
	}
	if err := writeFileAtomic(PendingConfigDBFile, config, 0644); err != nil {
		return fmt.Errorf("write %s: %w", PendingConfigDBFile, err)
	}
	slog.Info("staged vendor config for the new image", "path", PendingConfigDBFile)
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
