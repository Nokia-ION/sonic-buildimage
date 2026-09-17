// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"crypto/rsa"
	"crypto/sha512"
	"encoding/asn1"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"log/slog"
	"math/big"
	"os"
	"strings"

	"github.com/google/go-tpm/tpm2"
	"github.com/google/go-tpm/tpm2/transport"
	"github.com/google/go-tpm/tpm2/transport/linuxtpm"
)

const (
	DefaultTPMPath    = "/dev/tpmrm0"                 // default node for the TPM 2.0 i/f
	IDevIdContextPath = "/run/tpm/idevid_context.bin" // IDevId context file
)

// getIDevIDCertificate uses the provided TPM transport to read the
// IDevID certificate from the standard NV index.
func getIDevIDCertificate(t transport.TPM, name string, certIndex uint32) ([]byte, error) {
	slog.Info("reading IDevID cert",
		"name", name,
		"id", fmt.Sprintf("0x%08X", certIndex))

	// Read Public info to get the actual size of the certificate
	readPub := tpm2.NVReadPublic{
		NVIndex: tpm2.TPMHandle(certIndex),
	}
	pubRsp, err := readPub.Execute(t)
	if err != nil {
		return nil, fmt.Errorf("read IDevID NV public info: %w", err)
	}

	nvPublic, err := pubRsp.NVPublic.Contents()
	if err != nil {
		return nil, fmt.Errorf("parse NV public contents: %w", err)
	}

	// Determine Max Buffer size supported by this TPM to avoid overflow
	props := tpm2.GetCapability{
		Capability:    tpm2.TPMCapTPMProperties,
		Property:      uint32(tpm2.TPMPTNVBufferMax),
		PropertyCount: 1,
	}
	propRsp, err := props.Execute(t)
	if err != nil {
		return nil, fmt.Errorf("get TPM buffer properties: %w", err)
	}

	// Safely extract the max buffer size property
	tpmProps, err := propRsp.CapabilityData.Data.TPMProperties()
	if err != nil {
		return nil, fmt.Errorf("extract TPM properties: %w", err)
	}
	maxBuf := int(tpmProps.TPMProperty[0].Value)
	slog.Debug("extracted IDevID cert properties", "name", name)

	// Iteratively read the NV index (handling large certificates)
	size := int(nvPublic.DataSize)
	data := make([]byte, 0, size)
	offset := uint16(0)

	slog.Debug("reading IDevID certificate from TPM",
		"index", fmt.Sprintf("0x%X", certIndex),
		"total_size", size,
		"max_buffer", maxBuf)

	for len(data) < size {
		remaining := size - len(data)
		chunkSize := remaining
		if chunkSize > maxBuf {
			chunkSize = maxBuf
		}

		readCmd := tpm2.NVRead{
			AuthHandle: tpm2.AuthHandle{
				Handle: tpm2.TPMHandle(certIndex),
				Name:   pubRsp.NVName,
				Auth:   tpm2.PasswordAuth(nil),
			},
			NVIndex: tpm2.NamedHandle{
				Handle: tpm2.TPMHandle(certIndex),
				Name:   pubRsp.NVName,
			},
			Size:   uint16(chunkSize),
			Offset: offset,
		}

		readRsp, err := readCmd.Execute(t)
		if err != nil {
			return nil, fmt.Errorf("NV read failed at offset %d: %w", offset, err)
		}

		data = append(data, readRsp.Data.Buffer...)
		offset += uint16(chunkSize)
	}

	slog.Info("successfully read IDevID certificate from TPM",
		"index", fmt.Sprintf("0x%X", certIndex),
		"size_bytes", len(data))

	return data, nil
}

// resolveIDevIDCert tries to load a certificate from a file path first,
// but only if the path is specified and the file actually exists on disk.
func resolveIDevIDCert(tpm transport.TPM,
	name string,
	filePath string,
	nvIndex uint32) ([]byte, error) {
	// Check if the filename is specified (not empty)
	if filePath != "" {
		// Check if the file actually exists
		if _, err := os.Stat(filePath); err == nil {
			// File exists, attempt to read it
			data, err := os.ReadFile(filePath)
			if err == nil {
				slog.Info("resolved certificate from file",
					"name", name,
					"path", filePath,
					"size_bytes", len(data))
				return data, nil
			}
			slog.Warn("found certificate file but failed to read it",
				"path", filePath, "err", err)
		} else {
			// Some other error occurred (e.g., permission denied)
			slog.Warn("error checking certificate file status",
				"path", filePath, "err", err)
		}
	}

	// Fall back to TPM
	slog.Debug("falling back to TPM for certificate", "name", name,
		"index", fmt.Sprintf("0x%08X", nvIndex))
	return getIDevIDCertificate(tpm, name, nvIndex)
}

// ResolveIDevID acquires the IDevID credentials, handling multi-cert chains in both fields.
func ResolveIDevID(publicKeyFile, privateKeyFile string) (*rsa.PrivateKey, []byte, error) {
	// Simulation path: If files are provided, skip the TPM entirely.
	if publicKeyFile != "" && privateKeyFile != "" {
		slog.Info("using simulation IDevID", "pub", publicKeyFile, "priv", privateKeyFile)
		return LoadIDevIDFromDisk(publicKeyFile, privateKeyFile)
	}

	// Production path: check for IDevId certificate files that have been created, falling
	// back to reading the certs from the TPM directly
	slog.Info("accessing TPM for IDevID chain", "device", DefaultTPMPath)

	// Open the TPM device
	tpmDevice, err := linuxtpm.Open(DefaultTPMPath)
	if err != nil {
		return nil, nil, fmt.Errorf("open tpm at %s: %w", DefaultTPMPath, err)
	}
	defer tpmDevice.Close()

	target := GetIDevIDTargetInfo()
	slog.Info("initializing certificate NV indexes", "platform", target.Name)

	// Read Leaf Certificate (may be 1 or more certs)
	leaf, err := resolveIDevIDCert(
		tpmDevice, "leaf", target.LeafFile, target.LeafIndex)
	if err != nil {
		return nil, nil, fmt.Errorf("read IDevID leaf cert: %w", err)
	}
	if len(leaf) == 0 {
		return nil, nil, fmt.Errorf("read IDevID leaf cert: source is empty")
	}

	// Decode the leaf to check its contents
	var pemChain strings.Builder
	block, rest := pem.Decode(leaf)

	leafIsChain := false

	if block == nil {
		slog.Info("leaf certificate is raw DER; wrapping")
		pem.Encode(&pemChain, &pem.Block{Type: "CERTIFICATE", Bytes: leaf})
	} else {
		slog.Info("leaf certificate is PEM; preserving entire blob")
		pemChain.Write(leaf)
		// If there's non-whitespace data after the first PEM block, it's a chain
		if len(strings.TrimSpace(string(rest))) > 0 {
			leafIsChain = true
		}
	}

	// Process Intermediate (Only if leaf wasn't already a chain)
	if !leafIsChain && (target.InterFile != "" || target.InterIndex != 0) {
		intermediate, err := resolveIDevIDCert(
			tpmDevice, "intermediate", target.InterFile, target.InterIndex)

		// process each intermermediate cert (though should only be one)
		if err == nil && len(intermediate) > 0 {
			slog.Info("processing intermediate(s)")
			// Process Intermediate
			iBlock, _ := pem.Decode(intermediate)
			if iBlock == nil {
				slog.Info("intermediate is raw DER; wrapping")
				pem.Encode(&pemChain, &pem.Block{Type: "CERTIFICATE", Bytes: intermediate})
			} else {
				slog.Info("intermediate is PEM; preserving blob")
				pemChain.Write(intermediate)
			}
		} else if err != nil {
			slog.Warn("could not read configured intermediate", "err", err)
		} else if len(intermediate) == 0 {
			// unexpected, but as leaf exists, just issue a warning
			slog.Info("intermediate cert source returned no data; continuing with leaf only")
		}
	} else if leafIsChain {
		slog.Info("leaf already contains a chain; skipping intermediate check")
	}

	finalPEM := pemChain.String()
	certCount := strings.Count(finalPEM, "-----BEGIN CERTIFICATE-----")

	slog.Info("resolved IDevId certificate chain",
		"size_bytes", len(finalPEM),
		"cert_count", certCount)

	return nil, []byte(finalPEM), nil
}

// readContext reads the TPM IdevId context from a file
func readContext(t transport.TPM, path string) (tpm2.TPMHandle, error) {
	ctxFileData, err := os.ReadFile(path)
	if err != nil {
		return 0, fmt.Errorf("failed to read context file: %w", err)
	}

	if len(ctxFileData) < 18 { // 16 (Header) + 2 (Blob Size)
		return 0, fmt.Errorf("context file too small")
	}
	slog.Info("successfully read context file", "path", path, "bytes", len(ctxFileData))

	// The TPM Header (Sequence, Handle, Hierarchy)
	seq := binary.BigEndian.Uint64(ctxFileData[0:8])
	hnd := binary.BigEndian.Uint32(ctxFileData[8:12])
	hie := binary.BigEndian.Uint32(ctxFileData[12:16])

	// The ContextBlob starts at index 16.
	// Index 16-17 is the size. Skip these 2 bytes
	// because the go-tpm library adds them automatically.
	actualData := ctxFileData[18:]

	// Execute the load command
	loadCmd := tpm2.ContextLoad{
		Context: tpm2.TPMSContext{
			Sequence:    seq,
			SavedHandle: tpm2.TPMHandle(hnd),
			Hierarchy:   tpm2.TPMHandle(hie),
			ContextBlob: tpm2.TPM2BContextData{
				Buffer: actualData,
			},
		},
	}

	loadRsp, err := loadCmd.Execute(t)
	if err != nil {
		return 0, fmt.Errorf("TPM_RC_SIZE Fix Attempt (DataLen=%d): %w",
			len(actualData), err)
	}
	return loadRsp.LoadedHandle, nil
}

// signNonceChallenge signs a digest using the TPM and returns the DER encoded R|S signature.
// This is an internal helper used when in Production mode.
func signNonceChallenge(t transport.TPM,
	keyHandle tpm2.TPMHandle,
	dataToSign []byte) ([]byte, error) {
	// Prepare SHA-384 digest (Standard for Arista P-384 IDevID)
	digest := sha512.Sum384(dataToSign)

	// Configure the Signing Command
	signCmd := tpm2.Sign{
		KeyHandle: tpm2.NamedHandle{Handle: keyHandle},
		Digest:    tpm2.TPM2BDigest{Buffer: digest[:]},
		InScheme: tpm2.TPMTSigScheme{
			Scheme: tpm2.TPMAlgECDSA,
			Details: tpm2.NewTPMUSigScheme(
				tpm2.TPMAlgECDSA,
				&tpm2.TPMSSchemeHash{HashAlg: tpm2.TPMAlgSHA384},
			),
		},
		Validation: tpm2.TPMTTKHashCheck{
			Tag:       tpm2.TPMSTHashCheck,
			Hierarchy: tpm2.TPMRHNull,
		},
	}

	resp, err := signCmd.Execute(t)
	if err != nil {
		return nil, fmt.Errorf("tpm sign operation failed: %w", err)
	}

	// Extract Signature R and S
	eccSig, err := resp.Signature.Signature.ECDSA()
	if err != nil {
		return nil, fmt.Errorf("failed to extract ECDSA components from TPM response: %w", err)
	}

	// encode in DER format
	r := new(big.Int).SetBytes(eccSig.SignatureR.Buffer)
	s := new(big.Int).SetBytes(eccSig.SignatureS.Buffer)

	derSig, err := asn1.Marshal(ecdsaSignature{R: r, S: s})
	if err != nil {
		return nil, fmt.Errorf("failed to encode DER: %w", err)
	}

	return derSig, nil
}

// ResolveAndSignNonce signs a challenge based on the mode (Simulation vs Production).
// It returns the signature as a byte slice (DER encoded) or an error if any step fails.
func ResolveAndSignNonce(idevidPriv *rsa.PrivateKey, nonceHex string) ([]byte, error) {
	// Simulation Path: RSA signing
	if idevidPriv != nil {
		slog.Info("using simulation IDevID for signing challenge")
		return signNonceChallengeWithKey(idevidPriv, nonceHex)
	}

	//  Production Path: TPM signing
	slog.Info("accessing hardware TPM for IDevID signing")

	// Open the TPM device
	// Using /dev/tpm0 directly; if using a Resource Manager, swap to /dev/tpmrm0
	tpm, err := linuxtpm.Open(DefaultTPMPath)
	if err != nil {
		return nil, fmt.Errorf("could not open TPM device: %w", err)
	}
	defer tpm.Close()

	// Load the context from the binary file
	ctxPath := IDevIdContextPath
	keyHandle, err := readContext(tpm, ctxPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load IDevID context from %s: %w", ctxPath, err)
	}

	// Flush the transient handle to prevent TPM memory exhaustion.
	defer func() {
		if _, err := (tpm2.FlushContext{FlushHandle: keyHandle}).Execute(tpm); err != nil {
			slog.Warn("failed to flush IDevID handle from TPM",
				"handle", fmt.Sprintf("0x%08X", keyHandle), "err", err)
		}
	}()

	slog.Info("IDevID handle loaded successfully", "handle", fmt.Sprintf("0x%08X", keyHandle))

	// Sign the hex string bytes using the P-384 TPM logic
	signedNonce, err := signNonceChallenge(tpm, keyHandle, []byte(nonceHex))
	if err != nil {
		return nil, fmt.Errorf("failed to sign nonce challenge via TPM: %w", err)
	}

	slog.Info("nonce challenge signed successfully",
		"size_bytes", len(signedNonce),
		"hex_prefix", hex.EncodeToString(signedNonce)[:64],
	)
	return signedNonce, nil
}
