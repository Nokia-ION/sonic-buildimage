// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"context"
	"crypto/rsa"
	"crypto/tls"
	"fmt"
	"log/slog"
	"time"

	bpb "github.com/openconfig/bootz/proto/bootz"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

// BootzAttemptFunc defines the function signature for provisioning or reporting actions.
type BootzAttemptFunc func(
	context.Context,
	*Config,
	string,
	*bpb.ChassisDescriptor,
	*bpb.ControlCardState) error

// ConnectToBootz attempts to establish a TLS connection to a specific address.
// Don't present a cert and always accept the server cert.
// handles both IPv4 and IPv6 addresses
func ConnectToBootz(cfg *Config, addr string) (*grpc.ClientConn, error) {
	slog.Info("attempting to connect to bootz server", "address", addr)
	tlsConfig := &tls.Config{InsecureSkipVerify: true}

	// Use a timeout to avoid hanging forever on a dead IP
	ctx, cancel := context.WithTimeout(context.Background(), cfg.ConnTimeout)
	defer cancel()

	conn, err := grpc.DialContext(ctx, addr,
		grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)),
		grpc.WithBlock(), // Wait for the connection to actually establish
	)
	if err != nil {
		return nil, fmt.Errorf("connection failed to %s: %w", addr, err)
	}
	return conn, nil
}

// AuthenticateStream handles the IDevID identity and nonce challenge exchange.
// It returns an authenticated stream and the resolved public cert string.
func AuthenticateStream(
	ctx context.Context,
	cfg *Config,
	c bpb.BootstrapClient,
	idevidPriv *rsa.PrivateKey,
	initialReq *bpb.BootstrapStreamRequest,
) (bpb.Bootstrap_BootstrapStreamClient, error) {

	slog.Info("starting bootz stream")
	stream, err := c.BootstrapStream(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to start bootstrap stream: %w", err)
	}

	// Send the Initial Request (Identity + Request Type)
	slog.Info("sending initial bootstrap stream request")
	if err := stream.Send(initialReq); err != nil {
		return nil, fmt.Errorf("failed to send initial request: %w", err)
	}

	// Receive Nonce Challenge from server
	challengeResp, err := stream.Recv()
	if err != nil {
		return nil, fmt.Errorf("failed to receive challenge: %w", err)
	}
	challenge := challengeResp.GetChallenge()
	if challenge == nil {
		return nil, fmt.Errorf("expected challenge, got %v", challengeResp)
	}

	// Sign the Nonce
	signedNonce, err := ResolveAndSignNonce(idevidPriv, challenge.GetNonce())
	if err != nil {
		return nil, fmt.Errorf("failed to sign nonce: %w", err)
	}

	// Send the Signed Nonce back to the server
	authResp := &bpb.BootstrapStreamRequest{
		Type: &bpb.BootstrapStreamRequest_Response_{
			Response: &bpb.BootstrapStreamRequest_Response{
				Type: &bpb.BootstrapStreamRequest_Response_NonceSigned{
					NonceSigned: signedNonce,
				},
			},
		},
	}

	if err := stream.Send(authResp); err != nil {
		return nil, fmt.Errorf("failed to send signed nonce: %w", err)
	}

	return stream, nil
}

// ExecuteWithRetry orchestrates the connection attempts across all configured bootzAddresses
// for either provisioning or reporting. It derives a watchdog context from the provided
// maxDuration to enforce a strict global timeout across all operations, including
// network I/O, clock synchronization, and backoff periods.
//
// The function performs an exponential backoff (capped at 5 minutes) between full
// address-cycle retries. It returns nil upon a successful execution of attemptFunc,
// or a wrapped context error if the global deadline is exceeded or the parent
// context is cancelled.
func ExecuteWithRetry(ctx context.Context,
	cfg *Config,
	chassis *bpb.ChassisDescriptor,
	card *bpb.ControlCardState,
	attemptFunc BootzAttemptFunc) error {

	// Filter out invalid Uri schemes and normalize URIs to host:port
	cfg.BootzAddresses = DeduplicateStrings(
		FilterAndNormalizeBootzAddresses(cfg.BootzAddresses))
	if len(cfg.BootzAddresses) == 0 {
		return fmt.Errorf("no valid bootz server addresses available")
	}

	// Create a watchdog context derived from the parent context.
	// This ensures all operation  (NTP, gRPC, sleeps) respect the global limit and
	// remain responsive to parent cancellation (e.g., a manual shutdown signal).
	watchdogCtx, cancel := context.WithTimeout(ctx, cfg.MaxDuration)
	defer cancel()

	// Perform one-time setup (NTP/Addressing)
	// Clean up NTP Addresses and try clock-sync (only need to sync clock once)
	if len(cfg.NtpServers) == 0 {
		slog.Info("no NTP servers provided, skipping clock sync")
	} else {
		cfg.NtpServers = DeduplicateStrings(cfg.NtpServers)

		// If clock-sync fails, continue regardless as device clock may be correct
		if err := ClockSync(watchdogCtx, cfg); err != nil {
			slog.Warn("clock sync failed, continuing",
				"servers", cfg.NtpServers,
				"err", err)
		}
	}

	startTime := time.Now()
	backoff := 5 * time.Second
	cycle := 1

	for {
		// Try every address in the current cycle
		for i, addr := range cfg.BootzAddresses {
			// Check if context expired before starting work
			if watchdogCtx.Err() != nil {
				return fmt.Errorf("failed to %s: %w",
					cfg.Mode, watchdogCtx.Err())
			}

			slog.Info(cfg.Mode+"ing attempt",
				"attempt", i+1,
				"address", addr,
				"elapsed", time.Since(startTime).Truncate(time.Second),
			)

			if err := attemptFunc(watchdogCtx, cfg, addr, chassis, card); err == nil {
				return nil // Success
			} else {
				slog.Error(cfg.Mode+" attempt failed",
					"address", addr,
					"error", err)
			}
		}

		slog.Info("cycle failed, retrying", "cycle", cycle, "backoff", backoff)
		cycle += 1
		LogBanner("Cycle %d failed, retrying in %s", cycle, backoff)

		// Sleep or Exit based on Context
		// allowing interrupted graceful shutdowns
		select {
		case <-watchdogCtx.Done():
			// This triggers immediately if maxDuration is reached OR
			// if the parent context was cancelled.
			return fmt.Errorf("global timeout reached during %s: %w",
				cfg.Mode, watchdogCtx.Err())
		case <-time.After(backoff):
			// Exponential backoff logic & continue to next iteration
			backoff *= 2
			if backoff > 5*time.Minute {
				backoff = 5 * time.Minute
			}
		}
	}
}
