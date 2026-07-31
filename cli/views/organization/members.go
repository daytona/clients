// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package organization

import (
	"fmt"
	"sort"
	"strings"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/views/common"
	"github.com/daytona/clients/cli/views/util"
)

type MemberRowData struct {
	Name          string
	Email         string
	Role          string
	AssignedRoles string
	Joined        string
}

func ListOrganizationMembers(memberList []apiclient.OrganizationUser) {
	if len(memberList) == 0 {
		common.RenderInfoMessageBold("No members found")
		return
	}

	SortOrganizationMembers(&memberList)

	headers := []string{"Name", "Email", "Role", "Assigned Roles", "Joined"}

	data := [][]string{}

	for _, m := range memberList {
		var rowData *MemberRowData
		var row []string

		rowData = getMemberTableRowData(m)
		row = getRowFromMemberRowData(*rowData)
		data = append(data, row)
	}

	table := util.GetTableView(data, headers, nil, func() {
		renderUnstyledMemberList(memberList)
	})

	fmt.Println(table)
}

func SortOrganizationMembers(memberList *[]apiclient.OrganizationUser) {
	sort.Slice(*memberList, func(i, j int) bool {
		return (*memberList)[i].CreatedAt.Before((*memberList)[j].CreatedAt)
	})
}

func getMemberTableRowData(member apiclient.OrganizationUser) *MemberRowData {
	rowData := MemberRowData{"", "", "", "", ""}
	rowData.Name = member.Name + util.AdditionalPropertyPadding
	rowData.Email = member.Email
	rowData.Role = member.Role
	rowData.AssignedRoles = getAssignedRolesLabel(member.AssignedRoles)
	rowData.Joined = util.GetTimeSinceLabel(member.CreatedAt)

	return &rowData
}

func getAssignedRolesLabel(assignedRoles []apiclient.OrganizationRole) string {
	if len(assignedRoles) == 0 {
		return "-"
	}

	roleNames := []string{}
	for _, role := range assignedRoles {
		roleNames = append(roleNames, role.Name)
	}

	return strings.Join(roleNames, ", ")
}

func renderUnstyledMemberList(memberList []apiclient.OrganizationUser) {
	for _, member := range memberList {
		RenderMemberInfo(&member)

		if member.UserId != memberList[len(memberList)-1].UserId {
			fmt.Printf("\n%s\n\n", common.SeparatorString)
		}
	}
}

func RenderMemberInfo(member *apiclient.OrganizationUser) {
	var output string

	output += "\n"
	output += getInfoLine("Name", member.Name) + "\n"
	output += getInfoLine("Email", member.Email) + "\n"
	output += getInfoLine("Role", member.Role) + "\n"
	output += getInfoLine("Assigned Roles", getAssignedRolesLabel(member.AssignedRoles)) + "\n"
	output += getInfoLine("Joined", util.GetTimeSinceLabel(member.CreatedAt)) + "\n"

	renderUnstyledInfo(output)
}

func getRowFromMemberRowData(rowData MemberRowData) []string {
	row := []string{
		common.NameStyle.Render(rowData.Name),
		common.DefaultRowDataStyle.Render(rowData.Email),
		common.DefaultRowDataStyle.Render(rowData.Role),
		common.DefaultRowDataStyle.Render(rowData.AssignedRoles),
		common.DefaultRowDataStyle.Render(rowData.Joined),
	}

	return row
}
