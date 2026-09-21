#!/usr/bin/env python3
# Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
# Arista Networks, Inc. Confidential and Proprietary.

# pylint: disable=import-error, import-outside-toplevel

import json
import sys
import subprocess

def get_cli_output(cmd):
   """Helper to run a shell command and return output."""
   try:
      output = subprocess.check_output( cmd, shell=True, stderr=subprocess.DEVNULL )
      return output.decode( 'utf-8' ).strip()
   except Exception as e: # pylint: disable=broad-except
      sys.stderr.write( f"Failed to get Cli response: {e}")
      return ""

def get_chassis_metadata_via_cli():
   """Fallback using syseeprom."""
   eeprom = get_cli_output( "show platform syseeprom" )
   eeprom_data = {}
   for line in eeprom.splitlines():
      if ":" in line:
         k, v = line.split(":", 1)
         eeprom_data[ k.strip() ] = v.strip()

   model = eeprom_data.get( "SKU" )
   serial = eeprom_data.get( "SerialNumber")

   return {
      "part_number": model or "Unknown",
      "chassis_serial_number": serial or "Unknown",
   }

def get_chassis_metadata():
   """Attempt to read chassis metadata using the SONiC Platform API."""
   from sonic_platform.platform import Platform

   platform = Platform()
   chassis = platform.get_chassis()

   # Handle cases where methods might be missing or return None
   model = getattr( chassis, 'get_model', lambda: "Unknown" )()
   serial = getattr( chassis, 'get_serial', lambda: "Unknown" )()

   return {
      "part_number": model or "Unknown",
      "chassis_serial_number": serial or "Unknown",
   }

def main():
   """Extract SONiC chassis metadata."""
   try:
      # Primary method using Platform API
      metadata = get_chassis_metadata()

   except ( ImportError, AttributeError, RuntimeError ) as e:
      sys.stderr.write( f"Unexpected error: {str(e)}\n" )
      # If Platform fails, fallback to CLI
      metadata = get_chassis_metadata_via_cli()

   except Exception as e: # pylint: disable=broad-except
      sys.stderr.write( f"Unexpected error: {str(e)}\n" )
      metadata = {
         "part_number": "Unknown",
         "chassis_serial_number": "Unknown",
      }

   # Fixed chassis: bootz validates a global serial number instead of cards.
   metadata[ "control_cards" ] = []

   # Success, write the valid JSON to stdout
   print( json.dumps( metadata, indent=2 ) )

if __name__ == "__main__":
   main()
