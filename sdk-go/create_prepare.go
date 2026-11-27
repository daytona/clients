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

// maxIdempotencyKeyBytes is the longest idempotency key the API accepts.
const maxIdempotencyKeyBytes = 256

// idempotencyKeyText checks an explicit idempotency key (sandbox creates,
// reservations) as the Python and TypeScript SDKs do: a header value of at
// most 256 bytes.
func idempotencyKeyText(key string) error {
	if err := headerText(key, "IdempotencyKey"); err != nil {
		return err
	}
	if len(key) > maxIdempotencyKeyBytes {
		return badRequest("IdempotencyKey must be at most 256 bytes")
	}
	return nil
}

// randomIdempotencyKey is a lowercase UUID v4, the random key every SDK sends.
func randomIdempotencyKey() string {
	var raw [16]byte
	// Since Go 1.24 rand.Read never returns an error: it crashes the program
	// instead, so the buffer is never left zero-filled.
	_, _ = rand.Read(raw[:])
	raw[6] = raw[6]&0x0f | 0x40
	raw[8] = raw[8]&0x3f | 0x80
	text := hex.EncodeToString(raw[:])
	return text[0:8] + "-" + text[8:12] + "-" + text[12:16] + "-" + text[16:20] + "-" + text[20:]
}

func createIdempotencyKey(name, key string) string {
	if key != "" {
		return key
	}
	if name == "" {
		return randomIdempotencyKey()
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

// createEnv copies a create's Env, checked with UpdateEnv's rule.
func createEnv(input map[string]string) (map[string]string, error) {
	env := maps.Clone(input)
	for key, value := range env {
		if err := sandboxEnvKey(key); err != nil {
			return nil, err
		}
		if err := sandboxEnvValue(value); err != nil {
			return nil, err
		}
	}
	return env, nil
}

func prepareCreateAttachments(body *createBody, opts *CreateOptions) error {
	if opts.Env != nil {
		env, err := createEnv(opts.Env)
		if err != nil {
			return err
		}
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
