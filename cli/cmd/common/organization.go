// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"context"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/config"
)

// ListOrganizations returns the organizations the profile's user is a member of.
func ListOrganizations(profile config.Profile) ([]apiclient.Organization, error) {
	apiClient, err := apiclient_cli.GetApiClient(&profile, nil)
	if err != nil {
		return nil, err
	}

	organizationList, res, err := apiClient.OrganizationsAPI.ListOrganizations(context.Background()).Execute()
	if err != nil {
		return nil, apiclient_cli.HandleErrorResponse(res, err)
	}

	return organizationList, nil
}

func GetPersonalOrganizationId(profile config.Profile) (string, error) {
	organizationList, err := ListOrganizations(profile)
	if err != nil {
		return "", err
	}

	return PersonalOrganizationId(organizationList), nil
}

// PersonalOrganizationId is the id of the personal organization in the list, or "" when
// there is none.
func PersonalOrganizationId(organizationList []apiclient.Organization) string {
	for _, organization := range organizationList {
		if organization.Personal {
			return organization.Id
		}
	}

	return ""
}

func GetActiveOrganizationName(apiClient *apiclient.APIClient, ctx context.Context) (string, error) {
	activeOrganizationId, err := config.GetActiveOrganizationId()
	if err != nil {
		return "", err
	}

	if activeOrganizationId == "" {
		return "", config.ErrNoActiveOrganization
	}

	activeOrganization, res, err := apiClient.OrganizationsAPI.GetOrganization(ctx, activeOrganizationId).Execute()
	if err != nil {
		return "", apiclient_cli.HandleErrorResponse(res, err)
	}

	return activeOrganization.Name, nil
}
