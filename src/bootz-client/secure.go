// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"os"

	"log/slog"

	"go.mozilla.org/pkcs7"
)

// resolveManufCert returns the override path if provided, otherwise returns the platform default.
func resolveManufCert(cfgManufCertFile string, defaultManufCertFile string) string {
	// If the user provided a command-line override
	if cfgManufCertFile != "" {
		slog.Debug("using configured manufacturer certificate override",
			"path", cfgManufCertFile)
		return cfgManufCertFile
	}

	// Fall back to platform-specific default
	slog.Debug("using platform default manufacturer certificate",
		"path", defaultManufCertFile)
	return defaultManufCertFile
}

// LoadManufCA loads manufacturer's cert into a pool that will be used to verify
// Ownership-voucher signature
func LoadManufCA(cfg *Config) (*x509.CertPool, error) {
	target := GetManufTargetInfo()
	slog.Info("initializing manufacturer root trust", "platform", target.Name)

	// Determine which file path to use (Override vs Default)
	manufCertFile := resolveManufCert(cfg.ManufCertFile, target.ManufCertFile)

	// Try read file, handling all errors
	pemBytes, err := os.ReadFile(manufCertFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read vendor CA file: %w", err)
	}

	certPool := x509.NewCertPool()
	if ok := certPool.AppendCertsFromPEM(pemBytes); !ok {
		// Fallback: If not PEM, try parsing as raw DER
		cert, err := x509.ParseCertificate(pemBytes)
		if err != nil {
			return nil, fmt.Errorf("failed to parse vendor CA as PEM or DER")
		}
		certPool.AddCert(cert)
	}
	return certPool, nil
}

// GenerateNonce creates a cryptographically secure, random byte sequence
// for the bootstrap handshake. The nonce is included in the request and
// must be echoed back in the server's signed response to ensure freshness
// and protect against replay attacks. If the system's random number
// generator fails, this function will log a fatal error and terminate
// the client to prevent insecure provisioning.
func GenerateNonce() (string, error) {
	// Represents a 128 bit nonce.
	const nonceLength = 16

	b := make([]byte, nonceLength)
	_, err := rand.Read(b)
	if err != nil {
		return "", fmt.Errorf("crypto error: %w", err)
	}
	return base64.StdEncoding.EncodeToString(b), nil
}

// LoadIDevIDFromDisk handles the "simulation" case by reading cert+key from the filesystem.
// Read and parses an X.509 certificate and private key pair  to be used as the device's
// Initial Device Identity (IDevID).
func LoadIDevIDFromDisk(publicKeyFile, privateKeyFile string) (*rsa.PrivateKey, []byte, error) {
	// Read the Public Key file exactly as it is on disk
	pubKeyBytes, err := os.ReadFile(publicKeyFile)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to read public key file: %w", err)
	}

	// Read and Parse the Private Key for signing logic
	privKeyPEM, err := os.ReadFile(privateKeyFile)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to read private key file: %w", err)
	}

	block, _ := pem.Decode(privKeyPEM)
	if block == nil || block.Type != "RSA PRIVATE KEY" {
		return nil, nil, fmt.Errorf("failed to decode RSA private key PEM")
	}

	privKey, err := x509.ParsePKCS1PrivateKey(block.Bytes)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to parse private key: %w", err)
	}

	slog.Info("IDevID key pair read successfully",
		"public_key", publicKeyFile,
		"private_key", privateKeyFile,
	)
	return privKey, pubKeyBytes, nil
}

// signNonceChallengeWithKey signs a hex-encoded nonce using an RSA private key.
// It uses "Raw" PKCS#1 v1.5 signing (crypto.Hash(0)) to maintain
// compatibility with the existing OpenSSL/C++ server-side verification.
// Note. This is for simulation purposes only.
func signNonceChallengeWithKey(idevidPriv *rsa.PrivateKey, nonceHex string) ([]byte, error) {
	if idevidPriv == nil {
		return nil, fmt.Errorf("IDevID private key is nil")
	}

	slog.Info("processing nonce", "challenge", nonceHex, "length", len(nonceHex))

	// Sign the raw nonce characters (ASCII hex) directly.
	// Use crypto.Hash(0) so Go does not add a DigestInfo (SHA256 OID) header
	signedNonce, err := rsa.SignPKCS1v15(
		rand.Reader,
		idevidPriv,
		crypto.Hash(0),
		[]byte(nonceHex),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to sign nonce: %w", err)
	}

	slog.Info("nonce challenge signed successfully",
		"size_bytes", len(signedNonce),
		"hex_prefix", hex.EncodeToString(signedNonce)[:64],
	)

	return signedNonce, nil
}

// extractAndVerifyOC handles the degenerate CMS extraction and PDC trust validation.
func (bc *BootzContext) extractAndVerifyOC(ocRaw []byte, pdc *x509.Certificate) (*x509.Certificate, error) {
	if len(ocRaw) == 0 {
		return nil, fmt.Errorf("ownership certificate is empty")
	}

	// Manual PKCS7 Parsing to handle degenerate bundles (no signers)
	p7, err := pkcs7.Parse(ocRaw)
	if err != nil {
		return nil, fmt.Errorf("failed to parse OC PKCS7: %w", err)
	}

	if len(p7.Certificates) == 0 {
		return nil, fmt.Errorf("no certificates in OC bundle")
	}
	slog.Info("parsed ownership-certificate pkcs7 container")

	// Extract the leaf certificate (typically the first one)
	ocCert := p7.Certificates[0]
	slog.Info("ownership-certificate",
		"subject", ocCert.Subject,
		"issuer", ocCert.Issuer)

	// Verify trust chain: OC -> PDC
	roots := x509.NewCertPool()
	roots.AddCert(pdc)
	slog.Info("created trust pool with ownership-voucher PDC")
	if _, err := ocCert.Verify(x509.VerifyOptions{Roots: roots}); err != nil {
		return nil, fmt.Errorf("ownership certificate not trusted by PDC: %w", err)
	}

	slog.Info("validated ownership-certificate trusted by PDC")
	return ocCert, nil
}
