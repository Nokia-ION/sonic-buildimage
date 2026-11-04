// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"

	"log/slog"

	bpb "github.com/openconfig/bootz/proto/bootz"
)

const (
	ImagePath = "/tmp/bootz_downloaded_image.bin" // SWI image-file
)

// downloadImage fetches an OS image from HTTP/HTTPS and saves it locally.
// Returns: (finalPath, bytesWritten, error)
func downloadImage(ctx context.Context, cfg *Config, url string, localPath string) error {
	slog.Info("starting image download", "url", url, "path", localPath)

	// Configure Client & Transport
	dialer := &net.Dialer{
		Timeout:   cfg.HttpConnectTimeout,
		KeepAlive: 30 * time.Second,
	}

	// Setup insecure TLS client, have no local cert so accept whatever server uses
	tr := &http.Transport{
		DialContext:         dialer.DialContext,
		TLSClientConfig:     &tls.Config{InsecureSkipVerify: true},
		TLSHandshakeTimeout: cfg.HttpConnectTimeout,
	}

	// Setup Client with the download timeout
	client := &http.Client{
		Transport: tr,
		Timeout:   cfg.HttpDownloadTimeout,
	}

	// Prepare and Execute Request
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	// Validate Response Status First
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("server returned bad status: %s", resp.Status)
	}

	// Validate and Log Metadata
	if resp.ContentLength == 0 {
		return fmt.Errorf("server reported 0-byte file size (empty image)")
	}

	if resp.ContentLength > 0 {
		slog.Info("image metadata received",
			"size_bytes", resp.ContentLength,
			"size_mb", resp.ContentLength/(1024*1024),
		)
	} else {
		slog.Debug("image size unknown (chunked encoding or missing header)")
	}

	// Create Local File only after headers are validated
	out, err := os.Create(localPath)
	if err != nil {
		return fmt.Errorf("failed to create local file: %w", err)
	}

	// Defer a close as a safety net.
	defer out.Close()

	// Stream content to disk
	n, err := io.Copy(out, resp.Body)
	if err != nil {
		return fmt.Errorf("image transfer interrupted: %w", err)
	}

	// Post-download Validation
	if n == 0 {
		os.Remove(localPath)
		return fmt.Errorf("download complete but 0 bytes were written to disk")
	}

	if resp.ContentLength > 0 && n != resp.ContentLength {
		os.Remove(localPath)
		return fmt.Errorf("download size mismatch: got %d, expected %d", n, resp.ContentLength)
	}

	// Commit to physical storage
	if err := out.Sync(); err != nil {
		return fmt.Errorf("failed to sync image to disk: %w", err)
	}

	slog.Info("image downloaded successfully",
		"path", localPath,
		"size_bytes", n,
	)

	return nil
}

// validateImageHash reads the file at localPath, calculates its SHA-256 hash,
// and compares it against the expected hash string.
func validateImageHash(localPath, expectedHash, algorithm string) error {
	slog.Info("validating hash", "path", localPath)

	// Verify Algorithm - as per RFC8572, this is the only supported algorithm
	if algorithm != "ietf-sztp-conveyed-info:sha-256" {
		return fmt.Errorf("unsupported hash algorithm: %q", algorithm)
	}

	// Normalize the expected hash (remove colons)
	cleanExpectedHex := strings.ReplaceAll(expectedHash, ":", "")
	expectedBytes, err := hex.DecodeString(cleanExpectedHex)
	if err != nil {
		return fmt.Errorf("failed to decode expected hash %q: %w", expectedHash, err)
	}

	// Open the image file for hashing
	f, err := os.Open(localPath)
	if err != nil {
		return fmt.Errorf("failed to open image for validation: %w", err)
	}
	defer f.Close()

	// Calculate SHA-256 hash using a stream
	// (memory efficient method even for multi-gigabit files)
	hasher := sha256.New()
	if _, err := io.Copy(hasher, f); err != nil {
		return fmt.Errorf("failed to calculate hash: %w", err)
	}
	actualBytes := hasher.Sum(nil)

	// Compare and report
	if !bytes.Equal(actualBytes, expectedBytes) {
		actualHex := hex.EncodeToString(actualBytes)
		return fmt.Errorf(
			"hash mismatch for %s:\n  expected: %s\n  actual:   %s",
			localPath,
			expectedHash, // Keep the original colons for the error log
			actualHex,
		)
	}

	slog.Info("successfully verified hash", "image_file", localPath)
	return nil
}

// executeInstallCmd executes the image installer supporting self-extracting image
// install files, custom install scripts and standard SONiC install
func (bc *BootzContext) executeInstallCmd(
	ctx context.Context, cfg *Config, localPath string) (*exec.Cmd, error) {
	// Manual Override
	if cfg.InstallImageCmd != "" {
		if cfg.InstallImageCmd == selfExtractingSentinel {
			slog.Info("preparing self-extracting image file", "path", localPath)
			if err := os.Chmod(localPath, 0755); err != nil {
				return nil, fmt.Errorf("chmod +x failed: %w", err)
			}
			return exec.CommandContext(ctx, localPath), nil
		}

		// Custom command (primarily for testing purposes)
		slog.Info("preparing image install",
			"command", cfg.InstallImageCmd,
			"path", localPath)
		return exec.CommandContext(ctx, cfg.InstallImageCmd, localPath), nil
	}

	return ExecuteStandardInstaller(ctx, localPath)
}

// installImage executes the SONiC installer command to apply the validated image.
// It reports progress to the Bootz server before and after execution.
func (bc *BootzContext) installImage(ctx context.Context, cfg *Config, localPath string) error {
	bc.ReportInfoStatus("Installing SWI image")

	cmd, err := bc.executeInstallCmd(ctx, cfg, localPath)
	if err != nil {
		return err
	}
	if cmd == nil {
		slog.Warn("skipping image installer, no strategy found")
		return nil
	}

	// Capture both Stdout and Stderr to debug failures
	output, err := cmd.CombinedOutput()
	if err != nil {
		slog.Error("SWI install failed", "err", err, "output", string(output))
		return fmt.Errorf("install SWI: %w (output: %s)", err, output)
	}
	slog.Info("sonic installer completed successfully",
		"output", string(output))
	bc.imageInstalled = true

	// Trigger the reboot (if requested)
	bc.ReportInfoStatus("SWI installation successful")

	// The final SUCCESS report is sent by the installed image after reboot.
	if cfg.ImageFile == "" {
		if cfg.ConfigMode == ConfigModeMigrate {
			if err := backupRunningConfig(); err != nil {
				return fmt.Errorf("SWI installed but backing up the running config failed: %w", err)
			}
		} else if err := bc.stageNewImageVendorConfig(localPath); err != nil {
			return fmt.Errorf("SWI installed but staging vendor config failed: %w", err)
		}
		if err := stageTelemetryDir(); err != nil {
			slog.Error("failed to stage telemetry certificates", "err", err)
			bc.ReportInfoStatus("Existing telemetry certificates will not be carried over: %v", err)
		}
		if err := savePendingReport(cfg, bc.chassis); err != nil {
			slog.Error("failed to save pending status report", "err", err)
			bc.ReportInfoStatus("Final status will not be reported after reboot: %v", err)
		}
	}

	if err := Reboot(bc.config); err != nil {
		return fmt.Errorf("SWI installed but reboot failed: %w", err)
	}
	return nil
}

// ProcessIntendedImage manages the lifecycle of a software image update: checking versions,
// downloading the image, validating its integrity, and performing the installation.
func (bc *BootzContext) ProcessIntendedImage(
	ctx context.Context, cfg *Config, intendedImage *bpb.SoftwareImage) error {

	url := intendedImage.GetUrl()
	version := intendedImage.GetVersion()
	hash := intendedImage.GetOsImageHash()
	hashAlgo := intendedImage.GetHashAlgorithm()

	if url == "" {
		return fmt.Errorf("intended image URL is empty")
	}

	slog.Info("intended image",
		"version", version,
		"uri", url,
		"hash_algo", hashAlgo,
	)
	currentVersion := ReadCurrentVersion()
	slog.Info("checking current image", "version", currentVersion)

	if currentVersion == version {
		slog.Info("intended image version already installed", "version", version)
		bc.ReportInfoStatus("Intended image version already installed: %s", version)
		return nil
	}

	slog.Info("image version mismatch, starting download",
		"current", currentVersion,
		"intended", version,
	)
	bc.ReportInfoStatus("Image version mismatch (current %s, intended %s), starting download",
		currentVersion, version)

	imageFile := ImagePath
	if cfg.ImageFile != "" {
		slog.Info("overriding target image", "path", cfg.ImageFile)
		imageFile = cfg.ImageFile
	}

	err := downloadImage(ctx, bc.config, url, imageFile)
	if err != nil {
		if !bc.config.IgnoreImageError {
			return fmt.Errorf("unable to download image from %q: %w", url, err)
		}
		slog.Info("ignoring image download error", "err", err)
		bc.ReportInfoStatus("Image download failed but ignored per config")
		return nil
	}

	if err := validateImageHash(imageFile, hash, hashAlgo); err != nil {
		return fmt.Errorf("integrity validation failed for %s: %w", imageFile, err)
	}

	if err := bc.installImage(ctx, cfg, imageFile); err != nil {
		return fmt.Errorf("Image installation failed: %w", err)
	}

	slog.Info("image installation successful", "version", version, "uri", url)
	bc.ReportInfoStatus("Image installation successful, version %s, url %s", version, url)
	return nil
}
