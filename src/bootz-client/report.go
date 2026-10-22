// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"log/slog"

	bpb "github.com/openconfig/bootz/proto/bootz"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/encoding/prototext"
)

const (
	// Status levels for report messages
	StatusInfo     = "info"
	StatusError    = "error"
	StatusComplete = "complete"

	// Delimiter for status/message pairs
	StatusDelimiter = ":"

	// PendingReportFile tells the next boot to report completion to the Bootz
	// server. It must live on /host to survive the image install.
	PendingReportFile = "/host/bootz_pending_report.json"
)

// pendingReport holds the settings needed to report completion after reboot.
type pendingReport struct {
	BootzAddresses    []string `json:"bootz_addresses"`
	NtpServers        []string `json:"ntp_servers,omitempty"`
	ChassisDescriptor string   `json:"chassis_descriptor"`
	IdevidPublicCert  string   `json:"idevid_public_cert,omitempty"`
	IdevidPrivateKey  string   `json:"idevid_private_key,omitempty"`
}

// savePendingReport records how to reach the Bootz server so that the
// installed image can send the final status report once it is running.
func savePendingReport(cfg *Config, chassis *bpb.ChassisDescriptor) error {
	descriptor, err := prototext.Marshal(chassis)
	if err != nil {
		return fmt.Errorf("marshal chassis descriptor: %w", err)
	}

	data, err := json.MarshalIndent(pendingReport{
		BootzAddresses:    cfg.BootzAddresses,
		NtpServers:        cfg.NtpServers,
		ChassisDescriptor: string(descriptor),
		IdevidPublicCert:  cfg.IdevidCertFile,
		IdevidPrivateKey:  cfg.IdevidKeyFile,
	}, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal pending report: %w", err)
	}

	if err := os.WriteFile(PendingReportFile, data, 0600); err != nil {
		return fmt.Errorf("write %s: %w", PendingReportFile, err)
	}
	slog.Info("saved pending status report for next boot", "path", PendingReportFile)
	return nil
}

// buildReportStatusRequest constructs a Bootz-compliant ReportStatusRequest
// from the current chassis and card states.
func buildReportStatusRequest(
	chassis *bpb.ChassisDescriptor,
	status bpb.ReportStatusRequest_BootstrapStatus,
	msg string,
	cardStatus bpb.ControlCardState_ControlCardStatus) *bpb.ReportStatusRequest {

	// Map control cards to the current states
	cards := chassis.GetControlCards()
	states := make([]*bpb.ControlCardState, 0, len(cards)+1)
	for _, card := range cards {
		states = append(states, &bpb.ControlCardState{
			Status:       cardStatus,
			SerialNumber: card.GetSerialNumber(),
		})
	}

	// A fixed form factor chassis must report a single state matching the
	// chassis serial number, otherwise the server cannot record the status.
	if len(cards) == 0 {
		states = append(states, &bpb.ControlCardState{
			Status:       cardStatus,
			SerialNumber: chassis.GetSerialNumber(),
		})
	}

	return &bpb.ReportStatusRequest{
		Status:        status,
		StatusMessage: msg,
		States:        states,
	}
}

// sendReportStatus sends a status report to the Bootz server over the established stream
// and waits for a server acknowledgment.
// It returns an error if the send fails or if the server fails to acknowledge (ACK) the report.
func sendReportStatus(
	stream bpb.Bootstrap_BootstrapStreamClient,
	statusReq *bpb.ReportStatusRequest) error {

	// Prepare logging attributes from the request
	cardAttrs := make([]any, 0, len(statusReq.States)+1)
	cardAttrs = append(cardAttrs, "count", len(statusReq.States))
	for _, s := range statusReq.States {
		cardAttrs = append(cardAttrs, slog.Group("card",
			"serial", s.GetSerialNumber(),
			"status", s.GetStatus().String(),
		))
	}

	slog.Info("sending status report to server",
		"status", statusReq.Status.String(),
		"message", statusReq.StatusMessage,
		slog.Group("cards", cardAttrs...),
	)

	// Send via the bidirectional stream
	err := stream.Send(&bpb.BootstrapStreamRequest{
		Type: &bpb.BootstrapStreamRequest_ReportStatusRequest{
			ReportStatusRequest: statusReq,
		},
	})
	if err != nil {
		return fmt.Errorf("stream send failed: %w", err)
	}

	// Wait for server ACK
	resp, err := stream.Recv()
	if err != nil {
		return fmt.Errorf("failed to receive status report ACK: %w", err)
	}
	if resp.GetReportStatusResponse() == nil {
		return fmt.Errorf("server sent unexpected response type; expected ReportStatusResponse")
	}

	slog.Info("status report acknowledged",
		"message", statusReq.StatusMessage,
		slog.Group("cards", cardAttrs...),
	)

	return nil
}

// ReportInfoStatus is a helper function that sends an informational status
// message to the Bootz server.
//
// It uses BOOTSTRAP_STATUS_UNSPECIFIED and CONTROL_CARD_STATUS_UNINITIALIZED
// as defaults, making it suitable for progress updates that do not
// signify a final success or failure state.
func (bc *BootzContext) ReportInfoStatus(format string, args ...any) error {
	msg := format
	if len(args) > 0 {
		msg = fmt.Sprintf(format, args...)
	}
	req := buildReportStatusRequest(
		bc.chassis,
		bpb.ReportStatusRequest_BOOTSTRAP_STATUS_UNSPECIFIED,
		msg,
		bpb.ControlCardState_CONTROL_CARD_STATUS_NOT_INITIALIZED,
	)
	return sendReportStatus(bc.stream, req)
}

// ReportFailureStatus is a helper function that sends a failure status
// message to the Bootz server.
//
// It uses BOOTSTRAP_STATUS_FAILURE and CONTROL_CARD_STATUS_NOT_INITIALIZED
func (bc *BootzContext) ReportFailureStatus(msg string) error {
	req := buildReportStatusRequest(
		bc.chassis,
		bpb.ReportStatusRequest_BOOTSTRAP_STATUS_FAILURE,
		msg,
		bpb.ControlCardState_CONTROL_CARD_STATUS_NOT_INITIALIZED,
	)
	return sendReportStatus(bc.stream, req)
}

// ReportFinalStatus sends a final status report to the Bootz server indicating successful
// device provisioning.
// It sends a success notification, signaling that all artifacts have been validated,
// the intended-image has been installed, and the configuration has been successfully applied.
// This serves as confirmation that Bootz has completed it's provisioning tasks on the device.
func (bc *BootzContext) ReportFinalStatus() error {
	req := buildReportStatusRequest(
		bc.chassis,
		bpb.ReportStatusRequest_BOOTSTRAP_STATUS_SUCCESS,
		"Provisioning complete; device initialized",
		bpb.ControlCardState_CONTROL_CARD_STATUS_INITIALIZED,
	)
	return sendReportStatus(bc.stream, req)
}

// sendReportStream handles the streaming reporting workflow
func sendReportStream(
	ctx context.Context,
	cfg *Config,
	c bpb.BootstrapClient,
	req *bpb.ReportStatusRequest,
) (bpb.Bootstrap_BootstrapStreamClient, error) {

	// Resolve the IDevID using either simulated IDevId certs or actual
	idevidPriv, idevidPub, err := ResolveIDevID(cfg.IdevidCertFile, cfg.IdevidKeyFile)
	if err != nil {
		return nil, fmt.Errorf("resolve idevid: %w", err)
	}

	// Prepare the Identity
	req.Identity = &bpb.Identity{
		Type: &bpb.Identity_IdevidCert{IdevidCert: string(idevidPub)},
	}

	// Construct the wrapper report message
	reportReq := &bpb.BootstrapStreamRequest{
		Type: &bpb.BootstrapStreamRequest_ReportStatusRequest{
			ReportStatusRequest: req,
		},
	}

	// Handshake completes the report delivery on the server side
	return AuthenticateStream(ctx, cfg, c, idevidPriv, reportReq)
}

// resolveStatus maps a string level to the required Protobuf enums for both
// the Bootstrap report and the Control Card state.
func resolveStatus(level string) (bpb.ReportStatusRequest_BootstrapStatus, bpb.ControlCardState_ControlCardStatus) {
	switch level {
	case StatusComplete:
		return bpb.ReportStatusRequest_BOOTSTRAP_STATUS_SUCCESS, bpb.ControlCardState_CONTROL_CARD_STATUS_INITIALIZED
	case StatusError:
		return bpb.ReportStatusRequest_BOOTSTRAP_STATUS_FAILURE, bpb.ControlCardState_CONTROL_CARD_STATUS_NOT_INITIALIZED
	case StatusInfo:
		fallthrough
	default:
		return bpb.ReportStatusRequest_BOOTSTRAP_STATUS_UNSPECIFIED, bpb.ControlCardState_CONTROL_CARD_STATUS_UNSPECIFIED
	}
}

// parseReportMessage takes a raw string (e.g., "error: image hash comparison failure" or "just a message")
// and returns the status, the clean message, and the card status.
func parseReportMessage(rawMsg string) (
	bpb.ReportStatusRequest_BootstrapStatus,
	string,
	bpb.ControlCardState_ControlCardStatus) {
	defaultLevel := StatusInfo
	level, message, found := strings.Cut(rawMsg, StatusDelimiter)

	// If no colon is found, or the level provided isn't one of our allowed keywords,
	// treat the whole raw string as the message and use the default level.
	if !found || (level != StatusInfo && level != StatusError && level != StatusComplete) {
		bStatus, cStatus := resolveStatus(defaultLevel)
		return bStatus, rawMsg, cStatus
	}

	bStatus, cStatus := resolveStatus(level)
	return bStatus, message, cStatus
}

// runReporting handles the simplified flow for sending status reports.
// It resolves the IDevID, authenticates with the server, and sends the reports.
func runReporting(ctx context.Context,
	cfg *Config,
	conn *grpc.ClientConn,
	chassis *bpb.ChassisDescriptor,
	controlCard *bpb.ControlCardState) error {

	client := bpb.NewBootstrapClient(conn)

	//  Process the first message to initiate the stream/handshake
	bStatus, msg, cStatus := parseReportMessage(cfg.ReportMessages[0])

	slog.Info("sending initial report message", "msg", msg, "status", bStatus.String())

	// Update the controlCard pointer so discovery state is reflected in object state
	controlCard.Status = cStatus
	firstReq := buildReportStatusRequest(chassis, bStatus, msg, cStatus)

	stream, err := sendReportStream(ctx, cfg, client, firstReq)
	if err != nil {
		return err
	}

	// iterate over the remaining messages, sending each in turn
	for i, raw := range cfg.ReportMessages[1:] {
		bStatus, msg, cStatus := parseReportMessage(raw)

		slog.Info("sending additional report message",
			"msg", msg,
			"status", bStatus.String(),
			"index", i+1)

		controlCard.Status = cStatus
		nextReq := buildReportStatusRequest(chassis, bStatus, msg, cStatus)
		if err := sendReportStatus(stream, nextReq); err != nil {
			return fmt.Errorf("failed to send message %s(%d): %w", msg, i+1, err)
		}
	}

	// Graceful Teardown
	if err := stream.CloseSend(); err != nil {
		slog.Warn("failed to close report stream", "err", err)
	}

	// Drain stream until EOF
	for {
		_, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			slog.Warn("unexpected response after report", "err", err)
			break
		}
	}

	return nil
}

// AttemptReporting attempts to connect and send a status report to a bootz-server
func AttemptReporting(ctx context.Context,
	cfg *Config,
	addr string,
	chassis *bpb.ChassisDescriptor, // not used, but keeps func i/f consistent
	card *bpb.ControlCardState) error {
	conn, err := ConnectToBootz(cfg, addr)
	if err != nil {
		return err
	}
	defer conn.Close() // Ensures closure even if runReporting panics

	return runReporting(ctx, cfg, conn, chassis, card)
}
