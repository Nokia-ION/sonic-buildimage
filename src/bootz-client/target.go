// Copyright (c) 2026 Arista Networks, Inc.  All rights reserved.
// Arista Networks, Inc. Confidential and Proprietary.

package main

import "math/big"

// Platform specific IDevID configuration artifacts
type IDevIDTarget struct {
	LeafIndex  uint32
	InterIndex uint32
	LeafFile   string
	InterFile  string
	Name       string
}

type ecdsaSignature struct {
	R, S *big.Int
}

// Manufacturer certificates
type ManufTarget struct {
	ManufCertFile string
	Name          string
}
