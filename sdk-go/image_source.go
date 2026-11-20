// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// ImageSource is the closed set of source descriptions returned by the SDK.
// A future source kind is represented by UnknownImageSource.
type ImageSource interface {
	// Type identifies the source variant without inspecting concrete types.
	Type() ImageSourceType
	imageSource()
}

// ImageSourceType is a normalized source discriminator.
type ImageSourceType string

const (
	// ImageSourceTypeUnknown identifies a future kind; see UnknownImageSource.RawType.
	ImageSourceTypeUnknown ImageSourceType = "unknown"
	// ImageSourceTypeRegistry identifies an imported registry reference.
	ImageSourceTypeRegistry ImageSourceType = "registry"
	// ImageSourceTypeDockerfile identifies a Dockerfile build.
	ImageSourceTypeDockerfile ImageSourceType = "dockerfile"
	// ImageSourceTypeNamed identifies a named source.
	ImageSourceTypeNamed ImageSourceType = "named"
	// ImageSourceTypeManifest identifies a manifest source.
	ImageSourceTypeManifest ImageSourceType = "manifest"
)

// RegistryImageSource describes an imported registry reference.
type RegistryImageSource struct {
	// Ref is the registry reference.
	Ref string
}

func (RegistryImageSource) imageSource() {}

// Type returns ImageSourceTypeRegistry.
func (RegistryImageSource) Type() ImageSourceType { return ImageSourceTypeRegistry }

// DockerfileImageSource describes a Dockerfile build.
type DockerfileImageSource struct {
	// Dockerfile is the Dockerfile source.
	Dockerfile string
}

func (DockerfileImageSource) imageSource() {}

// Type returns ImageSourceTypeDockerfile.
func (DockerfileImageSource) Type() ImageSourceType { return ImageSourceTypeDockerfile }

// NamedImageSource describes a named image source.
type NamedImageSource struct {
	// Name is the source name.
	Name string
}

func (NamedImageSource) imageSource() {}

// Type returns ImageSourceTypeNamed.
func (NamedImageSource) Type() ImageSourceType { return ImageSourceTypeNamed }

// ManifestImageSource describes a manifest source.
type ManifestImageSource struct {
	// Name is the manifest name.
	Name string
}

func (ManifestImageSource) imageSource() {}

// Type returns ImageSourceTypeManifest.
func (ManifestImageSource) Type() ImageSourceType { return ImageSourceTypeManifest }

// UnknownImageSource preserves a source kind added by a newer API.
type UnknownImageSource struct {
	// RawType is the server's source discriminator.
	RawType string
}

func (UnknownImageSource) imageSource() {}

// Type returns ImageSourceTypeUnknown; RawType preserves the server kind.
func (UnknownImageSource) Type() ImageSourceType { return ImageSourceTypeUnknown }

func parseImageSource(raw JSONValue) (ImageSource, error) {
	object, err := parseObject(raw, "image source")
	if err != nil {
		return nil, err
	}
	kind, err := field[string](object, "type", true)
	if err != nil {
		return nil, err
	}
	switch kind {
	case "":
		return nil, badRequest("image source.type must be a non-empty string")
	case "registry":
		ref, parseErr := field[string](object, "ref", true)
		return RegistryImageSource{Ref: ref}, parseErr
	case "dockerfile":
		if _, parseErr := field[string](object, "context_object", true); parseErr != nil {
			return nil, parseErr
		}
		text, parseErr := field[string](object, "dockerfile", true)
		return DockerfileImageSource{Dockerfile: text}, parseErr
	case "named":
		name, parseErr := field[string](object, "name", true)
		return NamedImageSource{Name: name}, parseErr
	case "manifest":
		name, parseErr := field[string](object, "name", true)
		return ManifestImageSource{Name: name}, parseErr
	default:
		return UnknownImageSource{RawType: kind}, nil
	}
}
