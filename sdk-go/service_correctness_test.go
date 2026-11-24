// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// Region-filtered name lookup, label and name UTF-8 checks, volume
// get-or-create under a racing create, and the current API key projection.

var emptySearchPage = map[string]any{"sandboxes": []any{}, "total": 0, "total_relation": "eq", "next_cursor": nil}

func TestFindByName_whenRegionIsNamedFiltersEveryPage(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/search": func(w http.ResponseWriter, r *http.Request) {
		cursor, id := "next", "first"
		if r.URL.Query().Get("cursor") != "" {
			cursor, id = "", "second"
		}
		writeJSON(t, w, map[string]any{"sandboxes": []any{sandboxFixture(id)}, "total": 2, "total_relation": "eq", "next_cursor": cursor})
	}})
	client := newTestClient(t, fake.Server)
	// When
	items, err := client.FindByName(t.Context(), "my-name", &FindByNameOptions{Region: "region-a"})
	// Then
	require.NoError(t, err)
	require.Len(t, items, 2)
	for _, request := range fake.requests() {
		require.Equal(t, "region-a", request.Query.Get("region_ids"))
		require.Equal(t, "region-a", request.Header.Get("X-Daytona-Region"))
	}
}

func TestFindByName_whenRegionIsDefaultSearchesEveryRegion(t *testing.T) {
	for _, region := range []string{"", "  ", "local", " LOCAL "} {
		t.Run(region, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/search": jsonHandler(t, emptySearchPage)})
			client := newTestClient(t, fake.Server)
			// When
			items, err := client.FindByName(t.Context(), "n", &FindByNameOptions{Region: region})
			// Then
			require.NoError(t, err)
			require.Empty(t, items)
			request := fake.requests()[0]
			require.False(t, request.Query.Has("region_ids"))
			require.Empty(t, request.Header.Get("X-Daytona-Region"))
		})
	}
}

func TestFindByName_whenRegionNamesSeveralRefusesIt(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.FindByName(t.Context(), "n", &FindByNameOptions{Region: "eu,us"})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "region entries must be non-empty strings without commas")
	require.Empty(t, fake.requests())
}

func TestLabelsAndNames_whenNotValidUTF8AreRefusedBeforeAnyRequest(t *testing.T) {
	invalid := string([]byte{'a', 0xff})
	for _, tt := range []struct {
		name    string
		call    func(*Client, *Sandbox) error
		message string
	}{
		{"search label value", func(c *Client, _ *Sandbox) error {
			_, err := c.Search(t.Context(), &SearchSandboxesOptions{Labels: map[string]string{"k": invalid}})
			return err
		}, "label values must be valid UTF-8"},
		{"search label key", func(c *Client, _ *Sandbox) error {
			_, err := c.Search(t.Context(), &SearchSandboxesOptions{Labels: map[string]string{invalid: "v"}})
			return err
		}, "label keys must be valid UTF-8"},
		{"find by name", func(c *Client, _ *Sandbox) error {
			_, err := c.FindByName(t.Context(), invalid, nil)
			return err
		}, "name must be valid UTF-8"},
		{"create label value", func(c *Client, _ *Sandbox) error {
			_, err := c.Create(t.Context(), "img", &CreateOptions{Labels: map[string]string{"k": invalid}, Wait: NoWait})
			return err
		}, "label values must be valid UTF-8"},
		{"set labels key", func(_ *Client, s *Sandbox) error {
			_, err := s.SetLabels(t.Context(), map[string]string{invalid: "v"})
			return err
		}, "label keys must be valid UTF-8"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			// When
			err := tt.call(sandbox.client, sandbox)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tt.message)
			require.Empty(t, fake.requests())
		})
	}
}

func TestLabels_whenKeyIsEmptyReportsThatFirst(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Search(t.Context(), &SearchSandboxesOptions{Labels: map[string]string{"k": "\xff", "": "v"}})
	// Then
	require.ErrorContains(t, err, "label keys must not be empty")
}

func volumeError(t *testing.T, w http.ResponseWriter, status int, code string) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	writeJSON(t, w, map[string]string{"code": code, "source": "DAYTONA_API", "message": code})
}

func withoutRereadPauses(t *testing.T) {
	t.Helper()
	previous := volumeConflictRereadDelays
	volumeConflictRereadDelays = []time.Duration{0, 0, 0}
	t.Cleanup(func() { volumeConflictRereadDelays = previous })
}

func TestVolumesGet_whenCreateRacesReadsTheVolumeAgain(t *testing.T) {
	for _, tt := range []struct {
		name     string
		misses   int // reads after the conflict that still find nothing
		code     string
		reread   int // status of a read after the conflict
		want     error
		wantCode string
		requests int
	}{
		{"readable at once", 0, "CONFLICT", 200, nil, "", 3},
		{"readable after two misses", 2, "CONFLICT", 200, nil, "", 5},
		{"never readable", 3, "CONFLICT", 200, ErrConflict, "CONFLICT", 5},
		{"another conflict", 0, "REGION_NOT_EMPTY", 200, ErrConflict, "REGION_NOT_EMPTY", 2},
		{"failed read", 0, "CONFLICT", 403, ErrForbidden, "FORBIDDEN", 3},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			withoutRereadPauses(t)
			var reads atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /volumes/{ref}": func(w http.ResponseWriter, _ *http.Request) {
					read := int(reads.Add(1))
					switch {
					case read == 1 || read <= 1+tt.misses:
						volumeError(t, w, 404, "NOT_FOUND")
					case tt.reread != 200:
						volumeError(t, w, tt.reread, "FORBIDDEN")
					default:
						writeJSON(t, w, volumeFixture())
					}
				},
				"POST /volumes": func(w http.ResponseWriter, _ *http.Request) { volumeError(t, w, 409, tt.code) },
			})
			client := newTestClient(t, fake.Server)
			// When
			volume, err := client.Volumes().Get(t.Context(), "data", &GetVolumeOptions{Create: true})
			// Then
			if tt.want == nil {
				require.NoError(t, err)
				require.Equal(t, "vol_1", volume.ID)
			} else {
				require.ErrorIs(t, err, tt.want)
				var sdkErr *Error
				require.ErrorAs(t, err, &sdkErr)
				require.Equal(t, tt.wantCode, sdkErr.Code)
			}
			require.Len(t, fake.requests(), tt.requests)
		})
	}
}

func TestVolumesGet_whenCallersRaceAllReturnTheOneVolume(t *testing.T) {
	// Given every caller reads before any create lands: the first create wins
	// and every later one is the name conflict.
	const callers = 6
	var mu sync.Mutex
	volumes := map[string]any{}
	readers, creates := 0, 0
	allRead := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /volumes/{ref}": func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			volume, exists := volumes[r.PathValue("ref")]
			if !exists {
				readers++
				if readers == callers {
					close(allRead)
				}
			}
			mu.Unlock()
			if exists {
				writeJSON(t, w, volume)
				return
			}
			volumeError(t, w, 404, "NOT_FOUND")
		},
		"POST /volumes": func(w http.ResponseWriter, r *http.Request) {
			<-allRead
			var body struct{ Name string }
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Errorf("decode create body: %v", err)
				return
			}
			mu.Lock()
			creates++
			_, exists := volumes[body.Name]
			fixture := volumeFixture()
			fixture["name"] = body.Name
			if !exists {
				volumes[body.Name] = fixture
			}
			mu.Unlock()
			if exists {
				volumeError(t, w, 409, "CONFLICT")
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			writeJSON(t, w, fixture)
		},
	})
	client := newTestClient(t, fake.Server)
	// When
	ids := make([]string, callers)
	errs := make([]error, callers)
	var group sync.WaitGroup
	for index := range callers {
		group.Go(func() {
			volume, err := client.Volumes().Get(t.Context(), "shared", &GetVolumeOptions{Create: true})
			errs[index] = err
			if err == nil {
				ids[index] = volume.ID
			}
		})
	}
	group.Wait()
	// Then
	for index := range callers {
		require.NoError(t, errs[index])
		require.Equal(t, "vol_1", ids[index])
	}
	require.Equal(t, callers, creates)
	require.Len(t, volumes, 1)
}

func TestVolumesGet_whenContextEndsDuringRereadPauseReturnsCancellation(t *testing.T) {
	// Given a pause that outlasts the context, so the deadline lands in it.
	previous := volumeConflictRereadDelays
	volumeConflictRereadDelays = []time.Duration{time.Hour}
	t.Cleanup(func() { volumeConflictRereadDelays = previous })
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /volumes/{ref}": func(w http.ResponseWriter, _ *http.Request) { volumeError(t, w, 404, "NOT_FOUND") },
		"POST /volumes":      func(w http.ResponseWriter, _ *http.Request) { volumeError(t, w, 409, "CONFLICT") },
	})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithTimeout(t.Context(), 300*time.Millisecond)
	defer cancel()
	// When
	_, err := client.Volumes().Get(ctx, "data", &GetVolumeOptions{Create: true})
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.NotErrorIs(t, err, ErrConflict)
	require.Len(t, fake.requests(), 2)
}

func TestGetLimits_whenKeySyncFlagIsAbsentOrMistypedLooksUpTheKey(t *testing.T) {
	for name, set := range map[string]func(map[string]any){
		"absent": func(map[string]any) {},
		"null":   func(fixture map[string]any) { fixture["sync_to_v0"] = nil },
		"string": func(fixture map[string]any) { fixture["sync_to_v0"] = "yes" },
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fixture := accountKeyFixture()
			set(fixture)
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /api-keys/current":  jsonHandler(t, fixture),
				"GET /orgs/{org}/limits": jsonHandler(t, accountLimitsFixture()),
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Limits(t.Context(), nil)
			// Then
			require.NoError(t, err)
			require.True(t, strings.HasSuffix(fake.requests()[1].Path, "/orgs/org-id/limits"))
		})
	}
}
