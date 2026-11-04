// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"

	bpb "github.com/openconfig/bootz/proto/bootz"
	certz "github.com/openconfig/gnsi/certz"
	"go.mozilla.org/pkcs7"
)

const (
	// PendingCertzDir holds the Certz profiles received from the Bootz server
	// until they are installed. It must live on /host to survive the image
	// install, and it holds private keys, so it is only readable by root.
	PendingCertzDir = "/host/bootz_certz"

	// Layout used by the gNMI server's gNSI Certz service. The gNMI listener
	// presents the default profile; GNMI|certs in CONFIG_DB must point at
	// gnxiCertFile, gnxiKeyFile and gnxiCAFile for it to be used.
	telemetryCertDir   = "/etc/sonic/telemetry"
	certzMetaFile      = telemetryCertDir + "/grpc-version.json"
	certzLinkDir       = telemetryCertDir + "/certz_profiles"
	certzPersistDir    = "/etc/sonic/certz_profiles"
	gnmiDefaultProfile = "gnxi"
	gnxiCertFile       = telemetryCertDir + "/server_crt.cer"
	gnxiKeyFile        = telemetryCertDir + "/server_key.key"
	gnxiCAFile         = telemetryCertDir + "/ca_crt.cer"

	// Owner certs that the gNMI server's EnrollZ service installs.
	oIDevIDCertFile = telemetryCertDir + "/oIDevID_cert.pem"
	oIAKCertFile    = telemetryCertDir + "/oiak.crt"

	// Bootz forbids this ID in certz_profiles.
	reservedProfileID = "system_default_profile"

	stagedCertFile = "cert.pem"
	stagedKeyFile  = "key.pem"
	stagedCAFile   = "ca.pem"
)

var profileIDPattern = regexp.MustCompile(`^[a-zA-Z0-9._-]+$`)

// Names of the files a gNSI Certz rotation of the default profile writes into
// telemetryCertDir: <profile>_<version>_<ns>_{cert,key}.pem and
// ca_<profile>_<version>_<ns>_bundle.pem.
var (
	rotatedGnxiCertPattern = regexp.MustCompile(`^` + gnmiDefaultProfile + `_.+_[0-9]+_(cert|key)\.pem$`)
	rotatedGnxiCAPattern   = regexp.MustCompile(`^ca_` + gnmiDefaultProfile + `_.+_[0-9]+_bundle\.pem$`)
)

// stagedProfile is the PEM material of one Certz profile.
type stagedProfile struct {
	id   string
	cert []byte
	key  []byte
	ca   []byte
}

// collectCertzProfiles returns the Certz profiles in the bootstrap data. The
// deprecated 'certificates' field is treated as the gNMI default profile.
func collectCertzProfiles(data *bpb.BootstrapDataResponse) (map[string]*certz.UploadRequest, error) {
	profiles := make(map[string]*certz.UploadRequest)
	for _, p := range data.GetCertzProfiles().GetProfiles() {
		id := p.GetSslProfileId()
		if !profileIDPattern.MatchString(id) || id == "." || id == ".." || id == reservedProfileID {
			return nil, fmt.Errorf("invalid ssl_profile_id %q", id)
		}
		if _, dup := profiles[id]; dup {
			return nil, fmt.Errorf("duplicate ssl_profile_id %q", id)
		}
		profiles[id] = p.GetCertz()
	}

	if legacy := data.GetCertificates(); len(legacy.GetEntities()) > 0 {
		if _, ok := profiles[gnmiDefaultProfile]; ok {
			slog.Warn("ignoring deprecated certificates field, certz_profiles has the default profile",
				"profile", gnmiDefaultProfile)
		} else {
			profiles[gnmiDefaultProfile] = legacy
		}
	}
	return profiles, nil
}

// buildStagedProfile converts the Certz entities of one profile into PEM.
// idevidCertFile and idevidKeyFile are the file IDevID bootz-client was run
// with, if any. It returns the names of the entities that cannot be installed
// and were skipped.
func buildStagedProfile(id string, req *certz.UploadRequest, idevidCertFile, idevidKeyFile string) (*stagedProfile, []string, error) {
	sp := &stagedProfile{id: id}
	var skipped []string

	for _, e := range req.GetEntities() {
		switch ent := e.GetEntity().(type) {
		case *certz.Entity_CertificateChain:
			if sp.cert != nil {
				return nil, nil, fmt.Errorf("profile %q has more than one certificate chain", id)
			}
			cert, key, err := certChainPEM(ent.CertificateChain, idevidCertFile, idevidKeyFile)
			if err != nil {
				return nil, nil, fmt.Errorf("profile %q certificate chain: %w", id, err)
			}
			// The gNMI server signs with the TPM key only for the default profile.
			if key == nil && id != gnmiDefaultProfile {
				return nil, nil, fmt.Errorf("profile %q: the IDevID TPM key is only supported for profile %q",
					id, gnmiDefaultProfile)
			}
			sp.cert, sp.key = cert, key
		case *certz.Entity_TrustBundle:
			if sp.ca != nil {
				return nil, nil, fmt.Errorf("profile %q has more than one trust bundle", id)
			}
			ca, err := trustBundlePEM(ent.TrustBundle)
			if err != nil {
				return nil, nil, fmt.Errorf("profile %q trust bundle: %w", id, err)
			}
			sp.ca = ca
		case *certz.Entity_TrustBundlePkcs7, *certz.Entity_TrustBundlePcks7:
			if sp.ca != nil {
				return nil, nil, fmt.Errorf("profile %q has more than one trust bundle", id)
			}
			block := e.GetTrustBundlePkcs7().GetPkcs7Block()
			if block == "" {
				block = e.GetTrustBundlePcks7().GetPkcs7Block()
			}
			ca, err := pkcs7BundlePEM(block)
			if err != nil {
				return nil, nil, fmt.Errorf("profile %q PKCS#7 trust bundle: %w", id, err)
			}
			sp.ca = ca
		case *certz.Entity_CertificateRevocationListBundle:
			skipped = append(skipped, "certificate revocation list bundle")
		case *certz.Entity_AuthenticationPolicy:
			skipped = append(skipped, "authentication policy")
		case *certz.Entity_ExistingEntity:
			skipped = append(skipped, "existing entity reference")
		default:
			skipped = append(skipped, fmt.Sprintf("entity %T", ent))
		}
	}

	if sp.cert == nil && sp.ca == nil {
		return nil, skipped, nil
	}
	return sp, skipped, nil
}

// certChainPEM returns the PEM certificate chain (leaf first) and private key,
// and checks that the key matches the leaf certificate. For the IDevID with
// its TPM-resident key, it returns the IDevID chain and no key. When
// bootz-client runs with a file IDevID, the IDevID is that cert and key.
func certChainPEM(chain *certz.CertificateChain, idevidCertFile, idevidKeyFile string) ([]byte, []byte, error) {
	leaf := chain.GetCertificate()
	if leaf == nil {
		return nil, nil, fmt.Errorf("missing certificate")
	}
	if leaf.GetCertSource() == certz.Certificate_CERT_SOURCE_IDEVID &&
		idevidCertFile != "" && idevidKeyFile != "" {
		switch leaf.GetKeySource() {
		case certz.Certificate_KEY_SOURCE_UNSPECIFIED, certz.Certificate_KEY_SOURCE_IDEVID_TPM:
			return fileIDevIDPEM(idevidCertFile, idevidKeyFile)
		}
	}
	if leaf.GetCertSource() == certz.Certificate_CERT_SOURCE_IDEVID &&
		leaf.GetKeySource() == certz.Certificate_KEY_SOURCE_IDEVID_TPM {
		_, idevid, err := ResolveIDevID("", "")
		if err != nil {
			return nil, nil, fmt.Errorf("IDevID certificate: %w", err)
		}
		return idevid, nil, nil
	}
	if leaf.GetCertSource() != certz.Certificate_CERT_SOURCE_UNSPECIFIED {
		return nil, nil, fmt.Errorf("certificate source %s is not supported", leaf.GetCertSource())
	}
	if leaf.GetKeySource() != certz.Certificate_KEY_SOURCE_UNSPECIFIED {
		return nil, nil, fmt.Errorf("key source %s is not supported", leaf.GetKeySource())
	}

	var certPEM []byte
	for c := chain; c != nil; c = c.GetParent() {
		pemData, err := certificatePEM(c.GetCertificate())
		if err != nil {
			return nil, nil, err
		}
		certPEM = append(certPEM, pemData...)
	}

	rawKey := leaf.GetRawPrivateKey()
	if len(rawKey) == 0 {
		rawKey = leaf.GetPrivateKey()
	}
	if len(rawKey) == 0 {
		return nil, nil, fmt.Errorf("missing private key")
	}
	keyPEM, err := privateKeyPEM(rawKey)
	if err != nil {
		return nil, nil, err
	}

	if _, err := tls.X509KeyPair(certPEM, keyPEM); err != nil {
		return nil, nil, fmt.Errorf("private key does not match certificate: %w", err)
	}
	return certPEM, keyPEM, nil
}

// fileIDevIDPEM returns the file IDevID certificate chain and private key.
func fileIDevIDPEM(certFile, keyFile string) ([]byte, []byte, error) {
	certPEM, err := os.ReadFile(certFile)
	if err != nil {
		return nil, nil, fmt.Errorf("IDevID certificate: %w", err)
	}
	keyPEM, err := os.ReadFile(keyFile)
	if err != nil {
		return nil, nil, fmt.Errorf("IDevID private key: %w", err)
	}
	if _, err := tls.X509KeyPair(certPEM, keyPEM); err != nil {
		return nil, nil, fmt.Errorf("IDevID private key does not match certificate: %w", err)
	}
	slog.Info("using the file IDevID as the certz certificate", "cert", certFile, "key", keyFile)
	return certPEM, keyPEM, nil
}

// trustBundlePEM returns every certificate in the chain as PEM.
func trustBundlePEM(chain *certz.CertificateChain) ([]byte, error) {
	var out []byte
	for c := chain; c != nil; c = c.GetParent() {
		pemData, err := certificatePEM(c.GetCertificate())
		if err != nil {
			return nil, err
		}
		out = append(out, pemData...)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("empty trust bundle")
	}
	return out, nil
}

// pkcs7BundlePEM returns the certificates of a PEM-encoded PKCS#7 bundle as PEM.
func pkcs7BundlePEM(block string) ([]byte, error) {
	der := []byte(block)
	if p, _ := pem.Decode(der); p != nil {
		der = p.Bytes
	}
	p7, err := pkcs7.Parse(der)
	if err != nil {
		return nil, fmt.Errorf("parse: %w", err)
	}
	if len(p7.Certificates) == 0 {
		return nil, fmt.Errorf("bundle has no certificates")
	}
	var out []byte
	for _, c := range p7.Certificates {
		out = append(out, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c.Raw})...)
	}
	return out, nil
}

// certificatePEM validates one X.509 certificate entry (PEM or DER) and returns it as PEM.
func certificatePEM(c *certz.Certificate) ([]byte, error) {
	if c == nil {
		return nil, fmt.Errorf("missing certificate")
	}
	if t := c.GetType(); t != certz.CertificateType_CERTIFICATE_TYPE_X509 &&
		t != certz.CertificateType_CERTIFICATE_TYPE_UNSPECIFIED {
		return nil, fmt.Errorf("certificate type %s is not supported", t)
	}
	raw := c.GetRawCertificate()
	if len(raw) == 0 {
		raw = c.GetCertificate()
	}
	if len(raw) == 0 {
		return nil, fmt.Errorf("empty certificate")
	}

	var ders [][]byte
	if bytes.Contains(raw, []byte("-----BEGIN")) {
		for rest := raw; ; {
			var p *pem.Block
			p, rest = pem.Decode(rest)
			if p == nil {
				break
			}
			if p.Type == "CERTIFICATE" {
				ders = append(ders, p.Bytes)
			}
		}
		if len(ders) == 0 {
			return nil, fmt.Errorf("no PEM CERTIFICATE block found")
		}
	} else {
		ders = [][]byte{raw}
	}

	var out []byte
	for _, der := range ders {
		if _, err := x509.ParseCertificate(der); err != nil {
			return nil, fmt.Errorf("parse certificate: %w", err)
		}
		out = append(out, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})...)
	}
	return out, nil
}

// privateKeyPEM returns a PEM or DER private key as PEM.
func privateKeyPEM(raw []byte) ([]byte, error) {
	if p, _ := pem.Decode(raw); p != nil {
		return pem.EncodeToMemory(p), nil
	}
	if k, err := x509.ParsePKCS8PrivateKey(raw); err == nil {
		switch k.(type) {
		case *rsa.PrivateKey, *ecdsa.PrivateKey:
			return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: raw}), nil
		}
		return nil, fmt.Errorf("unsupported PKCS#8 private key type %T", k)
	}
	if _, err := x509.ParsePKCS1PrivateKey(raw); err == nil {
		return pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: raw}), nil
	}
	if _, err := x509.ParseECPrivateKey(raw); err == nil {
		return pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: raw}), nil
	}
	return nil, fmt.Errorf("unrecognized private key encoding")
}

// stageCertzProfiles validates the Certz profiles in the bootstrap data and
// writes them to PendingCertzDir. It returns the number of profiles staged.
func (bc *BootzContext) stageCertzProfiles(data *bpb.BootstrapDataResponse) (int, error) {
	if err := os.RemoveAll(PendingCertzDir); err != nil {
		return 0, fmt.Errorf("remove %s: %w", PendingCertzDir, err)
	}

	reqs, err := collectCertzProfiles(data)
	if err != nil {
		return 0, err
	}
	if len(reqs) == 0 {
		return 0, nil
	}

	ids := make([]string, 0, len(reqs))
	for id := range reqs {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	var staged []*stagedProfile
	for _, id := range ids {
		sp, skipped, err := buildStagedProfile(id, reqs[id], bc.config.IdevidCertFile, bc.config.IdevidKeyFile)
		if err != nil {
			return 0, err
		}
		for _, s := range skipped {
			slog.Warn("skipping unsupported certz entity", "profile", id, "entity", s)
			bc.ReportInfoStatus("Skipping unsupported certz entity in profile %s: %s", id, s)
		}
		if sp == nil {
			slog.Warn("certz profile has no certificate or trust bundle", "profile", id)
			continue
		}
		staged = append(staged, sp)
	}
	if len(staged) == 0 {
		return 0, nil
	}

	if err := writeStagedProfiles(staged); err != nil {
		os.RemoveAll(PendingCertzDir)
		return 0, err
	}
	return len(staged), nil
}

func writeStagedProfiles(staged []*stagedProfile) error {
	if err := os.MkdirAll(PendingCertzDir, 0700); err != nil {
		return fmt.Errorf("create %s: %w", PendingCertzDir, err)
	}
	for _, sp := range staged {
		dir := filepath.Join(PendingCertzDir, sp.id)
		if err := os.MkdirAll(dir, 0700); err != nil {
			return fmt.Errorf("create %s: %w", dir, err)
		}
		files := map[string][]byte{stagedCertFile: sp.cert, stagedKeyFile: sp.key, stagedCAFile: sp.ca}
		for name, data := range files {
			if data == nil {
				continue
			}
			if err := os.WriteFile(filepath.Join(dir, name), data, 0600); err != nil {
				return fmt.Errorf("write %s: %w", filepath.Join(dir, name), err)
			}
		}
		slog.Info("staged certz profile", "profile", sp.id,
			"certificate", sp.cert != nil, "tpm_key", sp.cert != nil && sp.key == nil,
			"trust_bundle", sp.ca != nil)
	}
	return nil
}

// InstallCertzProfiles installs the profiles staged in PendingCertzDir into the
// gNMI server's certificate directory, then removes the staged copy. The gNMI
// server picks them up when it (re)starts.
func InstallCertzProfiles() error {
	entries, err := os.ReadDir(PendingCertzDir)
	if os.IsNotExist(err) {
		slog.Info("no staged certz profiles", "path", PendingCertzDir)
		return nil
	}
	if err != nil {
		return fmt.Errorf("read %s: %w", PendingCertzDir, err)
	}

	var installed []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		id := e.Name()
		if !profileIDPattern.MatchString(id) || id == "." || id == ".." {
			return fmt.Errorf("invalid staged profile %q", id)
		}
		sp, err := readStagedProfile(id)
		if err != nil {
			return err
		}
		if id == gnmiDefaultProfile {
			err = installDefaultProfile(sp)
		} else {
			err = installCustomProfile(sp)
		}
		if err != nil {
			return fmt.Errorf("install certz profile %q: %w", id, err)
		}
		slog.Info("installed certz profile", "profile", id,
			"certificate", sp.cert != nil, "trust_bundle", sp.ca != nil)
		installed = append(installed, id)
	}

	if err := forgetCertzProfiles(installed); err != nil {
		return err
	}
	if err := os.RemoveAll(PendingCertzDir); err != nil {
		slog.Warn("failed to remove staged certz profiles", "path", PendingCertzDir, "err", err)
	}

	// On first boot gNMI has not started yet and this is a no-op.
	if out, err := exec.Command("systemctl", "--no-block", "try-restart", "gnmi.service").CombinedOutput(); err != nil {
		slog.Warn("failed to restart gnmi", "err", err, "output", string(out))
	}
	return nil
}

func readStagedProfile(id string) (*stagedProfile, error) {
	dir := filepath.Join(PendingCertzDir, id)
	sp := &stagedProfile{id: id}
	for name, dst := range map[string]*[]byte{stagedCertFile: &sp.cert, stagedKeyFile: &sp.key, stagedCAFile: &sp.ca} {
		data, err := os.ReadFile(filepath.Join(dir, name))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read staged %s: %w", filepath.Join(dir, name), err)
		}
		*dst = data
	}
	// A default profile certificate without a key uses the IDevID TPM key.
	if (sp.cert == nil) != (sp.key == nil) && (sp.cert == nil || id != gnmiDefaultProfile) {
		return nil, fmt.Errorf("staged profile %q must have both certificate and key", id)
	}
	return sp, nil
}

// installDefaultProfile writes the gNMI listener's certificate, key and CA,
// and removes the Certz links so the gNMI server re-reads them from GNMI|certs.
// Without a key, the key file is removed so that gNMI signs with the TPM.
func installDefaultProfile(sp *stagedProfile) error {
	if err := os.MkdirAll(telemetryCertDir, 0755); err != nil {
		return err
	}
	links := map[string]string{}
	if sp.cert != nil {
		if sp.key != nil {
			if err := writeFileAtomic(gnxiKeyFile, sp.key, 0600); err != nil {
				return err
			}
		}
		if err := writeFileAtomic(gnxiCertFile, sp.cert, 0644); err != nil {
			return err
		}
		if sp.key == nil {
			if err := os.Remove(gnxiKeyFile); err != nil && !os.IsNotExist(err) {
				return err
			}
		}
		links["server_cert.lnk"] = gnxiCertFile
		links["server_key.lnk"] = gnxiKeyFile
	}
	if sp.ca != nil {
		if err := writeFileAtomic(gnxiCAFile, sp.ca, 0644); err != nil {
			return err
		}
		links["ca_cert.lnk"] = gnxiCAFile
	}
	for link := range links {
		path := filepath.Join(telemetryCertDir, link)
		if fi, err := os.Lstat(path); err == nil && fi.Mode()&os.ModeSymlink != 0 {
			if err := os.Remove(path); err != nil {
				return err
			}
		}
	}
	removeOwnerCredentials(sp.cert != nil, sp.ca != nil)
	return nil
}

// removeOwnerCredentials deletes what an earlier owner left for the default
// profile once Bootz has replaced it: the Certz-rotated certificates and keys
// and the EnrollZ oIDevID and oIAK certs when cert is set, the Certz-rotated
// trust bundles when ca is set. The owner enrolls the device again after the
// Bootz SUCCESS report. Failures are logged; the files are no longer used.
func removeOwnerCredentials(cert, ca bool) {
	var stale []string
	if cert {
		stale = append(stale, oIDevIDCertFile, oIAKCertFile)
	}
	entries, err := os.ReadDir(telemetryCertDir)
	if err != nil {
		slog.Warn("failed to list old owner credentials", "path", telemetryCertDir, "err", err)
	}
	for _, e := range entries {
		if !e.Type().IsRegular() {
			continue
		}
		name := e.Name()
		if (cert && rotatedGnxiCertPattern.MatchString(name)) || (ca && rotatedGnxiCAPattern.MatchString(name)) {
			stale = append(stale, filepath.Join(telemetryCertDir, name))
		}
	}
	for _, path := range stale {
		err := os.Remove(path)
		if err == nil {
			slog.Info("removed old owner credential", "path", path)
		} else if !os.IsNotExist(err) {
			slog.Warn("failed to remove old owner credential", "path", path, "err", err)
		}
	}
}

// installCustomProfile lays out a non-default profile the way the gNMI Certz
// service restores it at startup: cert/key/ca links under certzLinkDir and a
// persistent copy of the CA under certzPersistDir.
func installCustomProfile(sp *stagedProfile) error {
	linkDir := filepath.Join(certzLinkDir, sp.id)
	persistDir := filepath.Join(certzPersistDir, sp.id)
	for _, d := range []string{linkDir, persistDir} {
		if err := os.MkdirAll(d, 0755); err != nil {
			return err
		}
	}

	type item struct {
		data []byte
		file string
		link string
		mode os.FileMode
	}
	items := []item{
		{sp.key, "bootz_key.key", "key", 0600},
		{sp.cert, "bootz_cert.cer", "cert", 0644},
		{sp.ca, "bootz_ca.cer", "ca", 0644},
	}
	for _, it := range items {
		if it.data == nil {
			continue
		}
		file := filepath.Join(linkDir, it.file)
		if err := writeFileAtomic(file, it.data, it.mode); err != nil {
			return err
		}
		if err := replaceSymlink(file, filepath.Join(linkDir, it.link)); err != nil {
			return err
		}
	}
	if sp.ca != nil {
		if err := writeFileAtomic(filepath.Join(persistDir, "ca"), sp.ca, 0600); err != nil {
			return err
		}
	}
	return nil
}

// forgetCertzProfiles drops the given profiles from the Certz metadata so the
// gNMI server rebuilds them from the installed files instead of stale paths.
func forgetCertzProfiles(ids []string) error {
	data, err := os.ReadFile(certzMetaFile)
	if os.IsNotExist(err) || len(ids) == 0 {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read %s: %w", certzMetaFile, err)
	}
	var meta map[string]json.RawMessage
	if err := json.Unmarshal(data, &meta); err != nil {
		slog.Warn("removing unreadable certz metadata", "path", certzMetaFile, "err", err)
		return os.Remove(certzMetaFile)
	}
	for _, id := range ids {
		delete(meta, id)
	}
	out, err := json.Marshal(meta)
	if err != nil {
		return err
	}
	mode := os.FileMode(0644)
	if fi, err := os.Stat(certzMetaFile); err == nil {
		mode = fi.Mode().Perm()
	}
	return writeFileAtomic(certzMetaFile, out, mode)
}

func writeFileAtomic(path string, data []byte, mode os.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func replaceSymlink(target, link string) error {
	tmp := link + ".tmp"
	os.Remove(tmp)
	if err := os.Symlink(target, tmp); err != nil {
		return err
	}
	return os.Rename(tmp, link)
}
