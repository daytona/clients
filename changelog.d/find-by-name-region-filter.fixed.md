**`find_by_name()` / `findByName()` / `FindByName` filter by the region you name** —
a named `region` now also sends the search's region filter, so only sandboxes in
that region are returned; before, it only selected the regional endpoint and
sandboxes from every region came back. An unknown region is now the API's 400
(`BAD_REQUEST`) instead of returning matches from every region. Omitting the
region, a blank value or `local` still searches every region. A region with a
comma is refused before any request.
