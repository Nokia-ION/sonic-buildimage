// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"

	"log/slog"

	bpb "github.com/openconfig/bootz/proto/bootz"
	"google.golang.org/protobuf/encoding/prototext"
)

const (
	// Default Inventory file that contains control-card info (JSON)
	defaultInventoryJSONPath = "/run/inventory.json"

	// Default location of the chassis metadata script.
	getChassisScript = "/usr/local/bin/bootz-get-chassis.py"
)

// formatSonicJsonToPrototext translates the SONiC Python script's JSON schema
// into a Bootz-compliant Prototext string. It returns an error if the JSON
// is malformed or if the script reported an internal platform error.
// Use intermediate prototext format for flexibility and logging reasons
func formatSonicJsonToPrototext(jsonData []byte) (string, error) {
	var data struct {
		Manufacturer string `json:"manufacturer"`
		Part         string `json:"part_number"`
		Serial       string `json:"chassis_serial_number"`
		ControlCards []struct {
			Serial string `json:"serial_number"`
			Slot   string `json:"slot_id"`
			Part   string `json:"part_number"`
		} `json:"control_cards"`
		Error string `json:"error"`
	}

	// Check for JSON unmarshaling errors
	if err := json.Unmarshal(jsonData, &data); err != nil {
		return "", fmt.Errorf("failed to parse sonic discovery json: %w", err)
	}

	// Check if the script itself reported a content error
	if data.Error != "" {
		return "", fmt.Errorf("sonic discovery script reported an internal error: %s", data.Error)
	}

	// XXX, default chassis serial-number & part-number pending resolution of Sonic discovery
	if data.Serial == "" {
		data.Serial = "<unknown>"
	}
	if data.Part == "" {
		data.Part = "<unknown>"
	}

	// default Manufacturer if unset
	if data.Manufacturer == "" {
		data.Manufacturer = "Nokia"
	}

	// Ensure have a serial number at least
	if data.Serial == "" {
		return "", fmt.Errorf("discovery json is missing mandatory chassis serial number")
	}

	slog.Info("parsed sonic discovery data",
		"manufacturer", data.Manufacturer,
		"part", data.Part,
		"serial", data.Serial,
		"cards_found", len(data.ControlCards))

	var sb strings.Builder
	fmt.Fprintf(&sb, "manufacturer: %q\n", data.Manufacturer)
	fmt.Fprintf(&sb, "part_number: %q\n", data.Part)
	fmt.Fprintf(&sb, "serial_number: %q\n", data.Serial)

	for i := range data.ControlCards {
		if data.ControlCards[i].Part == "" {
			data.ControlCards[i].Part = "<unknown>"
		}

		card := data.ControlCards[i]
		slog.Info("parsed control card details",
			"slot", card.Slot,
			"serial", card.Serial,
			"part_number", card.Part)

		sb.WriteString("control_cards {\n")
		fmt.Fprintf(&sb, "  serial_number: %q\n", card.Serial)
		fmt.Fprintf(&sb, "  slot_id: %q\n", card.Slot)
		fmt.Fprintf(&sb, "  part_number: %q\n", card.Part)
		sb.WriteString("}\n")
	}

	return sb.String(), nil
}

// discoverSonicChassis runs the platform discovery script to obtain the chassis and control
// card details and converts the resulting JSON into Prototext format.
func discoverSonicChassis(scriptPath string) (string, error) {
	slog.Info("executing sonic discovery script", "path", scriptPath)

	// Run the chassis discovery script
	out, err := exec.Command(scriptPath).Output()
	if err != nil {
		// If script exited 1, extract what it wrote to stderr
		if exitErr, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("discovery script error: %s", string(exitErr.Stderr))
		}
		return "", fmt.Errorf("discovery script failed: %w", err)
	}

	// Clean up output and format to Prototext
	// Trim the bytes directly to avoid extra string allocations
	cleanOut := bytes.TrimSpace(out)

	prototext, err := formatSonicJsonToPrototext(cleanOut)
	if err != nil {
		return "", fmt.Errorf("convert discovery JSON to prototext: %w", err)
	}

	return prototext, nil
}

// getChassisDescriptor prioritizes:
// - Explicit chassis-description string (cmd-line option)
// - Custom script flag (cmd-line option)
// - Dynamic SONiC discovery (default python script)
func getChassisDescriptor(cfg *Config) (string, error) {
	// Command-line explicit string override
	if cfg.ChassisDescriptor != "" {
		slog.Info("using supplied chassis descriptor")
		return cfg.ChassisDescriptor, nil
	}

	// Read Inventory file directly
	if cfg.ChassisScript == chassisReadSentinel {
		slog.Info("reading chassis info from JSON file", "path", defaultInventoryJSONPath)
		data, err := os.ReadFile(defaultInventoryJSONPath)
		if err != nil {
			return "", fmt.Errorf("failed to read SELF chassis file: %w", err)
		}
		return formatSonicJsonToPrototext(bytes.TrimSpace(data))
	}

	// Otherwise, run discovery script, first determine which script to run (Default vs. Custom)
	scriptPath := getChassisScript
	if cfg.ChassisScript != "" {
		scriptPath = cfg.ChassisScript
	}

	discovered, err := discoverSonicChassis(scriptPath)
	if err != nil {
		// Log the specific error from the script
		slog.Warn("chassis discovery script failed", "script", scriptPath, "err", err)
	} else {
		slog.Info("successfully retrieved chassis descriptor", "script", scriptPath)
		return discovered, nil
	}

	return "", fmt.Errorf("chassis discovery unavailable on this platform (tried: %s)", scriptPath)
}

// validateChassisDescriptor verifies that the chassis identity contains all
// mandatory Bootz fields. For modular systems, it ensures control cards
// have unique slot assignments and valid serial numbers. For fixed
// devices, it verifies a global chassis serial number is present.
func validateChassisDescriptor(chassis *bpb.ChassisDescriptor) error {
	if chassis.GetManufacturer() == "" || chassis.GetPartNumber() == "" {
		return fmt.Errorf("chassis missing required fields (Manufacturer/PartNumber): %v",
			chassis)
	}

	populatedSlots := make(map[string]bool)
	controlCards := chassis.GetControlCards()

	if len(controlCards) > 0 {
		for _, cc := range controlCards {
			slotID := cc.GetSlotId()
			if populatedSlots[slotID] {
				return fmt.Errorf(
					"chassis validation error: slot %s already in use", slotID)
			}
			populatedSlots[slotID] = true

			if cc.GetPartNumber() == "" {
				return fmt.Errorf(
					"control card in slot %s: missing PartNumber", slotID)
			}
			if cc.GetSerialNumber() == "" {
				return fmt.Errorf(
					"control card in slot %s: missing SerialNumber", slotID)
			}
		}
	}

	// For fixed chassis (no control cards), ensure chassis serial-number exists
	if chassis.GetSerialNumber() == "" {
		return fmt.Errorf("fixed chassis does not contain serial number: %v", chassis)
	}

	return nil
}

// DiscoverChassis probes the local system to identify hardware and returns
// a validated ChassisDescriptor. It returns an error if discovery,
// unmarshaling, or validation fails.
func DiscoverChassis(cfg *Config) (*bpb.ChassisDescriptor, error) {
	slog.Info("obtaining device details")

	descriptorStr, err := getChassisDescriptor(cfg)
	if err != nil {
		return nil, fmt.Errorf("chassis discovery: %w", err)
	}

	chassis := &bpb.ChassisDescriptor{}
	if err := prototext.Unmarshal([]byte(descriptorStr), chassis); err != nil {
		return nil, fmt.Errorf("unmarshal chassis descriptor: %w", err)
	}

	if err := validateChassisDescriptor(chassis); err != nil {
		return nil, fmt.Errorf("chassis validation: %w", err)
	}

	return chassis, nil
}

// GetActiveControlCard determines the identity and initial state of the primary
// processing unit. For modular systems, it identifies the serial number of
// the first available control card; for fixed form-factor switches, it
// defaults to the chassis's main serial number. The resulting state object
// is initialized with a 'NOT_INITIALIZED' status, signaling to the Bootz
// server that the hardware is ready for a fresh configuration deployment.
func GetActiveControlCard(chassis *bpb.ChassisDescriptor) *bpb.ControlCardState {
	state := &bpb.ControlCardState{
		Status: bpb.ControlCardState_CONTROL_CARD_STATUS_NOT_INITIALIZED,
	}
	if len(chassis.GetControlCards()) > 0 {
		state.SerialNumber = chassis.GetControlCards()[0].GetSerialNumber()
	} else {
		state.SerialNumber = chassis.GetSerialNumber()
	}
	slog.Info("active control card", "serial_number", state.GetSerialNumber())
	return state
}
