// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"

	"log/slog"
)

const (
	// Binary names
	ntpBinaryChronyd = "chronyd"
	ntpBinaryNtpd    = "ntpd"

	// Default configuration directories
	ntpDirChrony = "/etc/chrony"
	ntpDirNtpd   = "" // defaults to system tmp (/tmp)

	// Temp config file prefixes
	ntpPrefixChrony = "bootz-chrony.conf."
	ntpPrefixNtpd   = "bootz-ntp.conf."
)

// ResolveNTPBinary determines which binary to use for clock synchronization.
// It prioritizes user-provided overrides, otherwise it detects the platform
// and returns the appropriate default (chronyd for SONiC, ntpd otherwise).
func ResolveNTPBinary(userOverride string) string {
	target := ""
	if userOverride != "" {
		target = userOverride
	} else {
		target = GetDefaultNTPBinary()
	}

	// Try find the absolute path on the system
	path, err := exec.LookPath(target)
	if err != nil {
		slog.Warn("clock sync binary not found in PATH, using raw name",
			"target", target, "error", err)
		return target // Fallback to raw name if search fails
	}
	slog.Info("clock sync binary found in PATH", "target", target, "path", path)

	return path
}

// getNTPFlavor returns the config content, the command args, and the base directory/prefix
// for the temp config file based on whether using ntpd or chronyd.
func getNTPFlavor(binary string, servers []string) (
	content string, args []string, dir string, prefix string) {
	var configBuilder strings.Builder

	isChrony := strings.Contains(binary, ntpBinaryChronyd)
	isNtpd := strings.Contains(binary, ntpBinaryNtpd)

	if isChrony {
		dir = ntpDirChrony
		prefix = ntpPrefixChrony
		args = []string{"-q", "-U", "-f"}

		for _, server := range servers {
			configBuilder.WriteString(fmt.Sprintf("server %s iburst\n", server))
		}
		configBuilder.WriteString("makestep 1.0 -1\n")

	} else if isNtpd {
		dir = ntpDirNtpd
		prefix = ntpPrefixNtpd
		args = []string{"-gq", "-c"}

		for _, server := range servers {
			configBuilder.WriteString(fmt.Sprintf("server %s iburst minpoll 3 maxpoll 3\n", server))
		}

	} else {
		// overridden case
		// Return empty values to perform a "blind" execution of the binary.
		slog.Info("using overridden NTP binary; skipping config generation", "binary", binary)
		return "", []string{}, "", ""
	}

	return configBuilder.String(), args, dir, prefix
}

// ClockSync requests an immediate clock synchronization using the configured Clock-sync binary.
// Clock sync binary is one of:
// - chronyd
// - ntpd
// - other (testing)
func ClockSync(ctx context.Context, cfg *Config) error {
	if len(cfg.NtpServers) == 0 {
		return nil
	}

	ntpCtx, cancel := context.WithTimeout(ctx, cfg.NtpTimeout)
	defer cancel()

	configContent, cmdArgs, dir, prefix := getNTPFlavor(cfg.NtpCmd, cfg.NtpServers)

	// Only generate config file if a prefix was provided
	if prefix != "" {
		tmpConf, err := os.CreateTemp(dir, prefix)
		if err != nil {
			// Fallback to /tmp if the preferred dir (like /etc/chrony) is unavailable
			tmpConf, err = os.CreateTemp("", prefix)
			if err != nil {
				return fmt.Errorf("failed to create clock sync config: %w", err)
			}
		}
		defer os.Remove(tmpConf.Name())

		// Set permissions so dropped-privilege users can read it
		os.Chmod(tmpConf.Name(), 0644)

		if _, err := tmpConf.WriteString(configContent); err != nil {
			tmpConf.Close()
			return fmt.Errorf("failed to write to clock sync config: %w", err)
		}
		tmpConf.Sync()
		tmpConf.Close()

		slog.Debug("generated clock sync temp config file",
			"path", tmpConf.Name(),
			"content", "\n"+configContent)

		// Append the actual filename to the command args
		cmdArgs = append(cmdArgs, tmpConf.Name())
	}

	// Prepare the command for logging and execution
	fullCmd := cfg.NtpCmd
	if len(cmdArgs) > 0 {
		fullCmd += " " + strings.Join(cmdArgs, " ")
	}
	slog.Info("invoking clock sync", "command", fullCmd)

	// and execute command
	cmd := exec.CommandContext(ntpCtx, cfg.NtpCmd, cmdArgs...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		if ntpCtx.Err() == context.DeadlineExceeded {
			return fmt.Errorf("clock sync timed out after %v", cfg.NtpTimeout)
		}
		return fmt.Errorf("clock sync failed: %v, output: %s", err, string(output))
	}

	slog.Info("clock synchronization successful", "servers", cfg.NtpServers)
	return nil
}
