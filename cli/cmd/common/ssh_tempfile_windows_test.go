// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package common

import (
	"testing"
	"unsafe"

	"golang.org/x/sys/windows"
)

func TestCreatePrivateTempGrantsOnlyTheCurrentUser(t *testing.T) {
	invocation, err := newSSHInvocation(t.TempDir(), invocationTests[0].target)
	if err != nil {
		t.Fatalf("newSSHInvocation returned error: %v", err)
	}
	defer func() { _ = invocation.Cleanup() }()

	sd, err := windows.GetNamedSecurityInfo(invocation.ConfigPath, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatalf("reading the SSH config's security info: %v", err)
	}
	control, _, err := sd.Control()
	if err != nil {
		t.Fatal(err)
	}
	if control&windows.SE_DACL_PROTECTED == 0 {
		t.Error("SSH config DACL inherits entries from its folder")
	}

	dacl, _, err := sd.DACL()
	if err != nil {
		t.Fatal(err)
	}
	if dacl == nil {
		t.Fatal("SSH config has a NULL DACL, which grants everyone access")
	}
	if dacl.AceCount != 1 {
		t.Fatalf("SSH config DACL has %d entries, want 1 (current user only)", dacl.AceCount)
	}
	var ace *windows.ACCESS_ALLOWED_ACE
	if err := windows.GetAce(dacl, 0, &ace); err != nil {
		t.Fatal(err)
	}
	if ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE {
		t.Fatalf("SSH config DACL entry has type %d, want an access-allowed entry", ace.Header.AceType)
	}
	// FILE_ALL_ACCESS; Windows stores GENERIC_ALL on files in this mapped form.
	if ace.Mask != 0x1F01FF {
		t.Errorf("SSH config DACL entry grants %#x, want FILE_ALL_ACCESS (0x1f01ff)", ace.Mask)
	}
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	if sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart)); !sid.Equals(user.User.Sid) {
		t.Errorf("SSH config DACL grants %s, want the current user %s", sid, user.User.Sid)
	}
}
