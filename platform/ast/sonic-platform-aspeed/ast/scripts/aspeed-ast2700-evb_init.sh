#!/bin/bash

# Platform init script for Aspeed 2700 EVB

# Load required kernel-mode drivers
load_kernel_drivers() {

}

# - Main entry

# Install kernel drivers required for i2c bus access
load_kernel_drivers

# Disable sysrq-trigger
echo 0 > /proc/sys/kernel/sysrq

echo "Completed platform init script"
exit 0
