// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	flag "github.com/spf13/pflag"

	"log/slog"
)

// InitLogging sets up the global slog logger.
// If debug is true, it includes file and line numbers in every log entry.
func InitLogging(debug bool) {
	level := slog.LevelInfo
	if debug {
		level = slog.LevelDebug
	}

	opts := &slog.HandlerOptions{
		// AddSource: true includes the file and line number
		AddSource: debug,
		// Set the minimum log level
		Level: level,
	}

	handler := slog.NewTextHandler(os.Stdout, opts)

	// Set the global logger so slog.Info(...) works everywhere
	slog.SetDefault(slog.New(handler))
}

// LogStartupConfig logs the supplied command-line parameters
func LogStartupConfig(cfg *Config) {
	var setFlags []any

	flag.Visit(func(f *flag.Flag) {
		setFlags = append(setFlags, slog.String(f.Name, f.Value.String()))
	})

	if len(setFlags) > 0 {
		slog.Info("bootz-client started with explicit options",
			slog.Group("option", setFlags...),
		)
	} else {
		slog.Info("bootz-client started using all default values")
	}
}

// LogBanner prints a visually distinct, centered ASCII banner to the terminal.
// It uses a fixed width of 77 characters to separate major logical phases
// of the bootstrapping process, such as initialization, success, or failure.
func LogBanner(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)

	const totalWidth = 77
	line := strings.Repeat("=", totalWidth)

	// Protect against negative padding if msg is too long
	padding := (totalWidth - len(msg) - 2) / 2
	if padding < 0 {
		padding = 0
	}

	// Construct the center line
	// Use the same totalWidth logic to ensure the right-side padding
	// fills out the remainder of the 77 characters correctly.
	leftSide := strings.Repeat("=", padding)
	rightSideCount := totalWidth - len(leftSide) - len(msg) - 2

	if rightSideCount < 0 {
		rightSideCount = 0
	}
	rightSide := strings.Repeat("=", rightSideCount)

	fmt.Println(line)
	fmt.Printf("%s %s %s\n", leftSide, msg, rightSide)
	fmt.Println(line)
}

// FilterAndNormalizeBootzAddresses validates that strings are either raw host:port
// or bootz://host:port URIs. It discards invalid schemes and returns raw host:port.
func FilterAndNormalizeBootzAddresses(addrs []string) []string {
	var valid []string

	for _, raw := range addrs {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}

		// Check if it looks like a URI (contains ://)
		if strings.Contains(raw, "://") {
			u, err := url.Parse(raw)
			if err != nil {
				slog.Warn("discarding invalid Bootz URI", "input", raw, "error", err)
				continue
			}

			// Strictly enforce the "bootz" scheme
			if u.Scheme != "bootz" {
				slog.Warn("discarding URI with unsupported scheme", "input", raw, "scheme", u.Scheme)
				continue
			}

			// Extract the host:port (e.g., 127.0.0.1:50051)
			if u.Host != "" {
				valid = append(valid, u.Host)
			}
			continue
		}

		// If no scheme, assume it's a raw host:port.
		if _, _, err := net.SplitHostPort(raw); err != nil {
			slog.Warn("discarding invalid address format (expected host:port)", "input", raw)
			continue
		}

		valid = append(valid, raw)
	}

	return valid
}

// DeduplicateStrings returns a new slice containing only unique, non-empty
// strings from the input slice, preserving the original order.
func DeduplicateStrings(input []string) []string {
	var unique []string
	seen := make(map[string]bool)
	for _, s := range input {
		if s != "" && !seen[s] {
			seen[s] = true
			unique = append(unique, s)
		}
	}
	return unique
}

// IsSONiC checks if this is a SONiC device and caches the value for multiple calls
var IsSONiC = sync.OnceValue(func() bool {
	_, err := os.Stat(sonicVersionYaml)
	isSonic := err == nil

	slog.Info("platform detection complete",
		"is_sonic", isSonic,
		"detection_file", sonicVersionYaml)

	return isSonic
})

// EnsureNativeZTPSilenced verifies that the native ZTP service is down.
// It retries for a short duration to account for systemd shutdown latency.
func EnsureNativeZTPSilenced() error {
	slog.Info("ensuring ZTP service is not running", "service-name", ztpServiceName)
	for i := 0; i < 10; i++ {
		// Check systemd status
		cmd := exec.Command("systemctl", "is-active", ztpServiceName)
		out, _ := cmd.Output()
		status := strings.TrimSpace(string(out))

		// Check for the installer lock file
		_, err := os.Stat(sonicInstallerLock)
		lockExists := !os.IsNotExist(err)

		if status != "active" && !lockExists {
			slog.Info("native ztp inactive, lock file clear")
			return nil
		}

		slog.Warn("waiting for ztp shutdown", "status", status, "lock_exists", lockExists)
		time.Sleep(1 * time.Second)
	}
	return fmt.Errorf("native ztp service still active after timeout")
}

// Reboot triggers a system restart using the 'sudo reboot' command.
func Reboot(cfg *Config) error {
	// First check if the user explicitly requested to skip
	if cfg.SkipReboot {
		slog.Info("skipping reboot", "flag", cfg.SkipReboot)
		return nil
	}

	// Respect the test/simulation flag
	if cfg.ImageFile != "" {
		slog.Info("test mode: simulating system reboot (skipping actual command)")
		return nil
	}

	slog.Info("executing: sudo reboot")
	cmd := exec.Command("sudo", "reboot")

	// Use CombinedOutput to capture errors if the command fails to trigger
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to trigger reboot: %v, output: %s", err, string(output))
	}

	return nil
}

// JoinIfNotEmpty returns an empty string if the file is empty,
// otherwise it returns the joined path.
func JoinIfNotEmpty(dir, file string) string {
	if file == "" {
		return ""
	}
	return filepath.Join(dir, file)
}
