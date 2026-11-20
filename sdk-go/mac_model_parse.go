// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"time"
)

func parseMacResources(raw JSONValue, path string) (*MacResources, error) {
	object := accountResponse(raw, path)
	resources := &MacResources{
		Cores: object.integer("cores"), MemoryMiB: object.integer("memory_mib"), DiskMiB: object.integer("disk_mib"),
	}
	if *object.err != nil {
		return nil, *object.err
	}
	return resources, nil
}

func parseMacModel(raw JSONValue) (MacModel, error) {
	model := MacModel{}
	object, err := parseObject(raw, "Mac model")
	if err != nil {
		return model, err
	}
	for _, entry := range []struct {
		name   string
		target *string
	}{{"id", &model.ID}, {"name", &model.Name}} {
		*entry.target, err = field[string](object, entry.name, true)
		if err != nil {
			return model, err
		}
	}
	for _, entry := range []struct {
		name   string
		target *int64
	}{
		{"cores", &model.Cores},
		{"memory_mib", &model.MemoryMiB},
		{"disk_mib", &model.DiskMiB},
		{"sandbox_disk_mib", &model.SandboxDiskMiB},
		{"max_sandboxes", &model.MaxSandboxes},
	} {
		*entry.target, err = integerField(object, entry.name, true)
		if err != nil {
			return model, err
		}
	}
	if _, exists := object["default_size"]; !exists {
		return model, badRequest("response field default_size is required")
	}
	if present(object["default_size"]) {
		model.DefaultSize, err = parseMacResources(object["default_size"], "default_size")
		if err != nil {
			return model, err
		}
	}
	return model, err
}

func macPeriod(object jsonObject) (time.Duration, error) {
	seconds, err := integerField(object, "period_seconds", true)
	if err != nil {
		return 0, err
	}
	if seconds > math.MaxInt64/int64(time.Second) || seconds < math.MinInt64/int64(time.Second) {
		return 0, badRequest("response field period_seconds overflows time.Duration")
	}
	return time.Duration(seconds) * time.Second, nil
}

func parseMacPrice(raw JSONValue) (*MacPrice, error) {
	object, err := parseObject(raw, "Mac price")
	if err != nil {
		return nil, err
	}
	price := &MacPrice{}
	price.CentsPerHour, err = field[float64](object, "cents_per_hour", true)
	if err != nil {
		return nil, err
	}
	price.PeriodCents, err = integerField(object, "period_cents", true)
	if err != nil {
		return nil, err
	}
	price.Currency, err = field[string](object, "currency", true)
	if err != nil {
		return nil, err
	}
	price.Period, err = macPeriod(object)
	if err != nil {
		return nil, err
	}
	return price, nil
}

func parseMacCatalog(raw JSONValue) (*MacCatalog, error) {
	object, err := parseObject(raw, "Mac catalog")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, "regions", true)
	if err != nil {
		return nil, err
	}
	catalog := &MacCatalog{Regions: make([]MacCatalogRegion, 0, len(rows))}
	for _, row := range rows {
		region, parseErr := parseMacCatalogRegion(row)
		if parseErr != nil {
			return nil, parseErr
		}
		catalog.Regions = append(catalog.Regions, region)
	}
	return catalog, nil
}

func parseMacCatalogRegion(raw JSONValue) (MacCatalogRegion, error) {
	region := MacCatalogRegion{}
	object, err := parseObject(raw, "Mac catalog region")
	if err != nil {
		return region, err
	}
	region.ID, err = field[string](object, "id", true)
	if err != nil {
		return region, err
	}
	region.DefaultModel, err = field[string](object, "default_model", true)
	if err != nil {
		return region, err
	}
	rows, err := field[[]JSONValue](object, "models", true)
	if err != nil {
		return region, err
	}
	region.Models = make([]MacCatalogModel, 0, len(rows))
	for _, row := range rows {
		model, parseErr := parseMacCatalogModel(row)
		if parseErr != nil {
			return region, parseErr
		}
		region.Models = append(region.Models, model)
	}
	return region, nil
}

func parseMacCatalogModel(raw JSONValue) (MacCatalogModel, error) {
	entry := MacCatalogModel{}
	object, err := parseObject(raw, "Mac catalog model")
	if err != nil {
		return entry, err
	}
	if _, err = field[jsonObject](object, "model", true); err != nil {
		return entry, err
	}
	entry.Model, err = parseMacModel(object["model"])
	if err != nil {
		return entry, err
	}
	entry.Available, err = integerField(object, "available", true)
	if err != nil {
		return entry, err
	}
	if _, exists := object["price"]; !exists {
		return entry, badRequest("response field price is required")
	}
	if present(object["price"]) {
		entry.Price, err = parseMacPrice(object["price"])
	}
	return entry, err
}
