// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"maps"
	"strings"
	"time"
	"unicode"
)

const createMaxWireWait int64 = 2147483

func prepareCreateCommon(opts CreateFromSnapshotOptions) (preparedCreate, error) {
	result := preparedCreate{}
	if opts.Wait > 0 && opts.Wait%time.Second != 0 {
		return result, badRequest("create wait must be a whole number of seconds, NoWait, or WaitForever")
	}
	wait, err := lifecycleWaitBudget(opts.Wait)
	if err != nil {
		return result, err
	}
	result.wait = wait
	result.body.WaitSeconds = min(wireSeconds(wait), createMaxWireWait)
	if wait == WaitForever {
		result.body.WaitSeconds = createMaxWireWait
	}
	if identityErr := prepareCreateIdentity(&result.body, opts); identityErr != nil {
		return result, identityErr
	}
	result.body.Ports, err = createPorts(opts.Ports)
	if err != nil {
		return result, err
	}
	result.body.Lifecycle, err = lifecyclePolicyBody(opts.LifecyclePolicy, true)
	if err != nil {
		return result, err
	}
	result.body.CreateTimeout, err = createProvisionBudget(opts.CreateTimeout)
	if err != nil {
		return result, err
	}
	return result, nil
}

func createIdempotencyKey(name, key string) string {
	if key != "" {
		return key
	}
	if name == "" {
		return rand.Text()
	}
	literal := "name:" + name
	if len(literal) <= 256 && headerText(name, "name") == nil {
		return literal
	}
	digest := sha256.Sum256([]byte(name))
	return "name:sha256:" + hex.EncodeToString(digest[:])
}

func createTrim(value string) string {
	// Python str.strip also treats the four ASCII information separators as whitespace.
	return strings.TrimFunc(value, func(char rune) bool {
		return unicode.IsSpace(char) || (char >= '\x1c' && char <= '\x1f')
	})
}

func prepareCreateAttachments(body *createBody, opts *CreateOptions) error {
	if opts.Env != nil {
		env := maps.Clone(opts.Env)
		body.Env = &env
	}
	if opts.Volumes != nil {
		volumes := make([]createVolume, len(opts.Volumes))
		for index, volume := range opts.Volumes {
			if createTrim(volume.VolumeID) == "" || createTrim(volume.MountPath) == "" {
				return badRequest("Volumes require non-blank VolumeID and MountPath")
			}
			volumes[index] = createVolume{volume.VolumeID, volume.MountPath, volume.Subpath, volume.ReadOnly}
		}
		body.Volumes = &volumes
	}
	if opts.Secrets != nil {
		secrets := make([]string, len(opts.Secrets))
		for index, secret := range opts.Secrets {
			if secret == "" {
				return badRequest("secrets must contain non-empty secret names")
			}
			secrets[index] = secret
		}
		body.Secrets = &secrets
	}
	if opts.ClearNetworkPolicy {
		if opts.NetworkPolicy != nil {
			return badRequest("ClearNetworkPolicy cannot accompany NetworkPolicy")
		}
		body.Network = JSONValue("null")
	}
	if opts.NetworkPolicy != nil {
		policy, err := networkPolicyBody(*opts.NetworkPolicy)
		if err != nil {
			return err
		}
		encoded, err := json.Marshal(policy)
		if err != nil {
			return wrapError(err, "encode network policy")
		}
		body.Network = encoded
	}
	return nil
}
