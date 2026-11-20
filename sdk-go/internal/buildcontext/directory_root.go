// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"errors"
	"os"
)

func openDirectory(directory string, expected os.FileInfo) (root *os.Root, err error) {
	root, err = os.OpenRoot(directory)
	if err != nil {
		return nil, invalid("build context directory cannot be read: "+quote(directory), err)
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, root.Close())
			root = nil
		}
	}()
	opened, err := root.Stat(".")
	if err != nil {
		return root, err
	}
	if !os.SameFile(expected, opened) {
		return root, invalid("build context directory changed: "+quote(directory), nil)
	}
	return root, nil
}
