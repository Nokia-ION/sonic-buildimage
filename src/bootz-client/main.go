// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

// Sonic Bootz client implementation for TPM 2.0 with IDevId
package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	flag "github.com/spf13/pflag"

	"log/slog"

	bpb "github.com/openconfig/bootz/proto/bootz"
)

// global consts
const (
	// sonicVersionYaml is used to identify if this is a SONiC device
	sonicVersionYaml = "/etc/sonic/sonic_version.yml"

	// sonicInstallerLock is the global file-system lock used by the 'sonic-installer'
	// utility. This file must be  absent or unlocked before attempting
	// any SWI (Software Image) installation to avoid contention with native ZTP.
	sonicInstallerLock = "/var/run/sonic-installer.lock"

	// ztpServiceName is the systemd unit name for the native SONiC Zero Touch
	// Provisioning agent. This service must be stopped to release control of
	// the installer lock and ConfigDB during the Bootz flow.
	ztpServiceName = "ztp.service"

	// For self-extracting image install
	selfExtractingSentinel = "SELF"

	// For reading chassis info from inventory file(json)
	chassisReadSentinel = "SELF"
)

// global config struct
type Config struct {
	Verbose             bool
	InsecureBoot        bool
	BootzAddresses      []string
	NtpServers          []string
	NtpCmd              string
	NtpTimeout          time.Duration
	ChassisDescriptor   string
	ChassisScript       string
	ConfigDBFile        string
	ConfigInstaller     string
	ImageFile           string
	InstallImageCmd     string
	IgnoreImageError    bool
	IdevidCertFile      string
	IdevidKeyFile       string
	ManufCertFile       string
	ConnTimeout         time.Duration
	HttpTimeout         time.Duration
	HttpConnectTimeout  time.Duration
	HttpDownloadTimeout time.Duration
	MaxDuration         time.Duration
	SkipReboot          bool
	Mode                string
	ReportMessages      []string
	SendFinalReport     bool
	BootstrapJsonFile   string
	SilenceZTP          bool
}

// BootzContext encapsulates the active state of a Bootstrapping session.
// It maintains the long-lived gRPC bidirectional stream and the hardware
// metadata required to identify the device to the Bootz server.
type BootzContext struct {
	// stream is the bidirectional gRPC channel used for the 'GetBootstrapData'
	// exchange, carrying all traffic between the client and server.
	stream bpb.Bootstrap_BootstrapStreamClient

	// chassis contains the hardware-specific identifiers for this specific device.
	chassis *bpb.ChassisDescriptor

	// global config
	config *Config
}

// version info
var (
	version = "0.6.0" // Default, overridden from build script
)

// command-line args
var (
	insecureBoot = flag.BoolP(
		"insecure-boot", "r",
		false,
		"Start the client in non-secure mode.",
	)
	verbose = flag.BoolP(
		"verbose", "V",
		false,
		"Enable verbose logging with file and line numbers.",
	)
	bootzAddresses = flag.StringArrayP(
		"bootz-addr", "a",
		[]string{},
		"List of Bootz server addresses.",
	)
	ntpServers = flag.StringArrayP(
		"ntp-addr", "n",
		[]string{},
		"List of NTP server addresses.",
	)
	ntpCmd = flag.StringP(
		"ntp-cmd", "N",
		"",
		"NTP override: Alternative program to invoke for NTP.",
	)
	ntpTimeout = flag.DurationP(
		"ntp-timeout", "w",
		10*time.Second,
		"Timeout for the NTP/Clock sync operation.",
	)
	chassisDescriptor = flag.StringP(
		"chassis-descriptor", "d",
		"",
		"Chassis override: Textproto formatting of the chassis descriptor.",
	)
	chassisScript = flag.StringP(
		"chassis-script", "s",
		chassisReadSentinel,
		"Chassis override: Path to a script that returns the chassis descriptor, "+
			"or 'SELF' to read from "+defaultInventoryJSONPath,
	)
	configDBFile = flag.StringP(
		"config-db", "c",
		"/etc/sonic/config_db.json",
		"Location of target startup config file.",
	)
	configInstaller = flag.StringP(
		"config-installer", "i",
		"",
		"Path to an executable to process/install the vendor configuration file.",
	)
	imageFile = flag.StringP(
		"image-file", "f",
		ImagePath,
		"Location of target image file",
	)
	installImageCmd = flag.StringP(
		"install-cmd", "I",
		selfExtractingSentinel,
		"Install override: Alternative program for image install "+
			"(use 'SELF' for self-extraction).",
	)
	ignoreImageError = flag.BoolP(
		"ignore-image-error", "G",
		false,
		"Continue provisioning even if OS image download fails.",
	)
	idevidCertFile = flag.StringP(
		"idevid-public-cert", "E",
		"",
		"Identity override: Path to the IDevID public certificate file.",
	)
	idevidKeyFile = flag.StringP(
		"idevid-private-key", "K",
		"",
		"Identity override: Path to the IDevID private key file.",
	)
	manufCertFile = flag.StringP(
		"manuf-cert", "m",
		"",
		"Manufacturer cert override: Path to the manufacturer cert",
	)
	connTimeout = flag.DurationP(
		"timeout", "t",
		10*time.Second,
		"Timeout for each Bootz connection attempt.",
	)
	httpConnectTimeout = flag.DurationP(
		"http-connect-timeout", "C",
		10*time.Second,
		"Timeout for the initial HTTP connection attempt.",
	)
	httpDownloadTimeout = flag.DurationP(
		"http-download-timeout", "T",
		5*time.Minute,
		"Maximum time allowed for the entire image download.",
	)
	maxDuration = flag.DurationP(
		"max-duration", "x",
		1*time.Hour,
		"Total time for provisioning before forcing a reboot.",
	)
	skipReboot = flag.BoolP(
		"skip-reboot", "o",
		false,
		"Do not reboot the device after successful provisioning.",
	)
	mode = flag.StringP(
		"mode", "M",
		"provision",
		"Operation mode: 'provision' for full run, 'report' to send a status message.",
	)
	reportMessages = flag.StringArrayP(
		"report-message", "R",
		[]string{},
		fmt.Sprintf("Status and message pair. Format: '<status>%s<message>', where status is "+
			"one of %s, %s or %s.", StatusDelimiter, StatusInfo, StatusError, StatusComplete),
	)
	sendFinalReport = flag.BoolP(
		"send-final-report", "F",
		false,
		"Send a final 'SUCCESS' status report upon successful provisioning.",
	)
	bootstrapJsonFile = flag.StringP(
		"bootstrap-json-file", "j",
		"",
		"Path to write the received BootstrapDataResponse (JSON).",
	)
	showVersion = flag.BoolP(
		"version", "v",
		false,
		"Print version and exit",
	)
	silenceZTP = flag.BoolP(
		"silence-ztp", "Z",
		false,
		"Silence competing ZTP services before starting.",
	)
)

func printVersion() {
	fmt.Fprintf(os.Stderr, "bootz-client v%s\n", version)
	fmt.Fprintf(os.Stderr, "Copyright (c) 2026 Arista Networks. All rights reserved.\n")
}

// printCustomFlag is a helper function to print specific flags by name
func printCustomFlag(w io.Writer, names ...string) {
	for _, name := range names {
		f := flag.Lookup(name)
		if f == nil {
			continue
		}

		// Construct the flag name and shorthand part
		var namePart string
		if f.Shorthand != "" {
			namePart = fmt.Sprintf("-%s, --%s", f.Shorthand, f.Name)
		} else {
			namePart = fmt.Sprintf("    --%s", f.Name)
		}

		// Construct the usage part + default value
		usage := f.Usage
		if f.DefValue != "" && f.DefValue != "[]" && f.DefValue != "false" {
			usage = fmt.Sprintf("%s (default: %s)", usage, f.DefValue)
		}

		// Print formatted line: %-30s provides a fixed width for the names
		fmt.Fprintf(w, "  %-30s %s\n", namePart, usage)
	}
}

// setupFlagsConfig defines a custom usage function and parses the config flags
func setupFlagsConfig() {
	// Define the Custom Usage Help
	flag.Usage = func() {
		printVersion()
		fmt.Fprintf(os.Stderr, "\nUsage:\n\n")

		fmt.Fprintln(os.Stderr, "MANDATORY PARAMETERS:")
		printCustomFlag(os.Stderr, "bootz-addr")

		fmt.Fprintln(os.Stderr, "\nMODES & REPORTING:")
		printCustomFlag(os.Stderr,
			"mode",
			"report-message",
			"send-final-report")

		fmt.Fprintln(os.Stderr, "\nCONFIG INSTALL PARAMETERS:")
		printCustomFlag(os.Stderr,
			"config-db",
			"config-installer")

		fmt.Fprintln(os.Stderr, "\nOPERATIONAL PARAMETERS:")
		printCustomFlag(os.Stderr,
			"skip-reboot",
			"ntp-addr",
			"ntp-timeout",
			"max-duration",
			"timeout",
			"verbose",
			"bootstrap-json-file",
			"version")

		fmt.Fprintln(os.Stderr, "\nHTTP & IMAGE DOWNLOAD AND INSTALL SETTINGS:")
		printCustomFlag(os.Stderr,
			"image-file",
			"install-cmd",
			"http-connect-timeout",
			"http-download-timeout",
			"ignore-image-error")

		fmt.Fprintln(os.Stderr, "\nTESTING & DEVELOPMENT:")
		printCustomFlag(os.Stderr,
			"manuf-cert",
			"chassis-script", "chassis-descriptor",
			"idevid-public-cert", "idevid-private-key",
			"insecure-boot",
			"ntp-cmd",
			"silence-ztp")
		fmt.Fprintln(os.Stderr)
	}

	// Parse the flags from the command line
	flag.Parse()
}

// validateFlags checks the command-line arguments for logical consistency.
// It returns an error with a user-friendly message if validation fails.
func validateFlags() error {
	// Check Mandatory Bootz Address
	if len(*bootzAddresses) == 0 {
		return fmt.Errorf("mandatory option --bootz-addr (-a) is missing")
	}

	// Check Simulated IDevID Cert and Key (Both or Neither)
	if (*idevidCertFile == "") != (*idevidKeyFile == "") {
		if *idevidCertFile == "" {
			return fmt.Errorf("provided --idevid-key but missing --idevid-cert")
		}
		return fmt.Errorf("provided --idevid-cert but missing --idevid-key")
	}

	switch *mode {
	case "report", "provision":
		// valid
	default:
		return fmt.Errorf("invalid --mode %q; must be 'provision', or 'report'", *mode)
	}

	// Logic for reporting mode
	if *mode == "report" {
		// Ensure at least one message is provided
		if reportMessages == nil || len(*reportMessages) == 0 {
			return fmt.Errorf("--report-message (-R) is required when mode is 'report'")
		}

		// Validate the prefix of each message if a colon is present
		for _, raw := range *reportMessages {
			level, _, found := strings.Cut(raw, ":")
			if found {
				switch level {
				case StatusInfo, StatusError, StatusComplete:
					// valid prefix
				default:
					// valid (as will treat entire string as info message), but issue a warning
					// in case this isn't the intent
					slog.Warn("unrecognised status prefix in report message",
						"level", level,
						"raw_input", raw,
						"expected", []string{StatusInfo, StatusError, StatusComplete})
				}
			}
		}
	}
	return nil
}

// main serves as the primary entry point for the Bootz Sonic Client.
// It initializes system logging, parses command-line flags, and
// ensures that native ZTP processes are disabled to prevent
// resource contention.
//
// The function discovers local hardware identities and delegates the gRPC
// provisioning/reporting.
// If in provisioning mode, upon successful provisioning/reporting or
// a watchdog timeout, the system initiates a reboot to apply the new
// configuration or reset the state.
// If in reporting more, client exits.
func main() {
	start := time.Now()

	// Initialize an empty root context.
	// Constraints are applied further down the call stack
	ctx := context.Background()

	setupFlagsConfig()
	if *showVersion {
		printVersion()
		os.Exit(0)
	}

	// Verify platform is correct
	if IsSONiC() != IsSonicSupported() {
		slog.Error("Platform Mismatch",
			"Actual_IsSonic", IsSONiC(),
			"Binary_IsSonicSupported", IsSonicSupported())

		fmt.Printf("FATAL: This %s binary cannot run on this platform.\n", PlatformName)
		os.Exit(1)
	}

	slog.Info("Platform check passed", "target", PlatformName)

	// validate command-line options
	if err := validateFlags(); err != nil {
		fmt.Fprintf(os.Stderr, "Configuration Error: %v\n\n", err)
		flag.Usage()
		os.Exit(1)
	}

	InitLogging(*verbose)

	// initialise global config
	cfg := &Config{
		Verbose:             *verbose,
		InsecureBoot:        *insecureBoot,
		BootzAddresses:      *bootzAddresses,
		NtpServers:          *ntpServers,
		NtpCmd:              *ntpCmd,
		NtpTimeout:          *ntpTimeout,
		ChassisDescriptor:   *chassisDescriptor,
		ChassisScript:       *chassisScript,
		ConfigDBFile:        *configDBFile,
		ConfigInstaller:     *configInstaller,
		ImageFile:           *imageFile,
		InstallImageCmd:     *installImageCmd,
		IgnoreImageError:    *ignoreImageError,
		IdevidCertFile:      *idevidCertFile,
		IdevidKeyFile:       *idevidKeyFile,
		ManufCertFile:       *manufCertFile,
		ConnTimeout:         *connTimeout,
		HttpConnectTimeout:  *httpConnectTimeout,
		HttpDownloadTimeout: *httpDownloadTimeout,
		MaxDuration:         *maxDuration,
		SkipReboot:          *skipReboot,
		Mode:                *mode,
		ReportMessages:      *reportMessages,
		SendFinalReport:     *sendFinalReport,
		BootstrapJsonFile:   *bootstrapJsonFile,
		SilenceZTP:          *silenceZTP,
	}
	LogStartupConfig(cfg)

	// internal logic/platform resolution
	cfg.NtpCmd = ResolveNTPBinary(cfg.NtpCmd)

	if cfg.Verbose {
		LogBanner("Bootz Sonic Client")
	}

	if cfg.SilenceZTP {
		if err := EnsureNativeZTPSilenced(); err != nil {
			slog.Error("native ztp active", "err", err, "action", "aborting")
			os.Exit(2)
		}
	}

	chassis, err := DiscoverChassis(cfg)
	if err != nil {
		slog.Error("failed to identify hardware platform", "err", err)
		os.Exit(1)
	}
	slog.Info("platform identified",
		"manufacturer", chassis.GetManufacturer(),
		"serial", chassis.GetSerialNumber())

	controlCard := GetActiveControlCard(chassis)

	// Select the Action based on mode
	var attemptFunc func(context.Context, *Config, string, *bpb.ChassisDescriptor, *bpb.ControlCardState) error

	if cfg.Mode == "report" {
		attemptFunc = AttemptReporting
	} else {
		attemptFunc = AttemptProvisioning
	}

	// Execute
	err = ExecuteWithRetry(ctx, cfg, chassis, controlCard, attemptFunc)
	duration := time.Since(start).Truncate(time.Second)

	if err != nil {
		slog.Error(cfg.Mode+" failed, watchdog triggered",
			"duration", duration,
			"err", err,
			"server", cfg.BootzAddresses,
		)

		if cfg.Mode == "provision" {
			Reboot(cfg)
		}
		os.Exit(1)
	}

	slog.Info(cfg.Mode+" complete",
		"duration", duration,
		"chassis_sn", chassis.SerialNumber,
		"status", "success",
	)

	if cfg.Verbose {
		LogBanner("Bootz %sing Complete", strings.Title(cfg.Mode))
	}

	// Mode-specific exit behavior
	if cfg.Mode == "provision" {
		if err := Reboot(cfg); err != nil {
			slog.Error("reboot failed", "err", err, "status", "provisioning_complete")
			os.Exit(1)
		}
	}
}
