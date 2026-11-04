// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.
//go:build !eos

package main

import (
	"context"
	"log/slog"
	"os/exec"
	"strings"
)

const (
	PlatformName = "SONiC"

	// Sonic cert definitions
	SonicIDevIDLeafCertIndex  = 0x01C90000         // IDevID Leaf cert NV Index
	SonicIDevIDInterCertIndex = 0                  // No intermediate cert in the TPM
	SonicIDevIDKeyHandle      = 0x81820000         // Persistent IDevID key handle
	SonicCertPath             = "/etc/certs"       // IDevid cert file directory
	SonicIDevIDLeafCertFile   = "idevid-chain.crt" // IDevID Leaf cert file
	SonicIDevIDInterCertFile  = ""                 // IDevID intermediate cert file

	// Manuf root cert for verifying ownership-voucher signatures
	SonicManufCertFile = "/etc/certs/ARISTA_ROOT_CA.crt"
)

// IsSonicSupported returns true because this binary was built for SONiC.
func IsSonicSupported() bool {
	return true
}

// GetTargetInfo populates platform specific config structure
func GetIDevIDTargetInfo() IDevIDTarget {
	return IDevIDTarget{
		LeafIndex:  SonicIDevIDLeafCertIndex,
		InterIndex: SonicIDevIDInterCertIndex,
		LeafFile:   JoinIfNotEmpty(SonicCertPath, SonicIDevIDLeafCertFile),
		InterFile:  JoinIfNotEmpty(SonicCertPath, SonicIDevIDInterCertFile),
		Name:       "SONiC",
	}
}

// GetManufTargetInfo populates platform specific config structure
func GetManufTargetInfo() ManufTarget {
	return ManufTarget{
		ManufCertFile: SonicManufCertFile,
		Name:          "SONiC",
	}
}

// ReadCurrentVersion attempts to retrieve the SONiC version string.
// It first checks for the existence of the SONiC version config file.
// If found, it runs 'sonic-cfggen' to get the version.
// Returns a default value if not running on SONiC or if the command fails.
func ReadCurrentVersion() string {
	const versionFile = "/etc/sonic/sonic_version.yml"

	// Execute sonic-cfggen to get the build version
	// -v flag queries the variable 'build_version' from the version file
	out, err := exec.Command("sonic-cfggen", "-v", "build_version", "-y", versionFile).Output()
	if err != nil {
		// If the command fails, try a simpler fallback (caching version file)
		return "SONiC-Unknown-Version"
	}

	// Clean up the output (remove newlines/spaces)
	version := strings.TrimSpace(string(out))
	if version == "" {
		return "SONiC-Development-Build"
	}

	return version
}

// GetDefaultNTPBinary gets the default NTPD binary
func GetDefaultNTPBinary() string {
	return ntpBinaryChronyd
}

// ExecuteStandardInstaller executes standard SWI installer. Without -y,
// sonic-installer prompts for confirmation, which aborts with no terminal.
func ExecuteStandardInstaller(ctx context.Context, localPath string) (*exec.Cmd, error) {
	slog.Info("executing installer",
		"command", "sonic-installer",
		"path", localPath)
	return exec.CommandContext(ctx, "sudo", "sonic-installer", "install", "-y", localPath), nil
}
