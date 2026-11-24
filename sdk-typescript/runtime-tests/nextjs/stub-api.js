// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Local stand-in for the parts of the Daytona API and object storage that creating a
// sandbox from a Dockerfile context touches. It records the uploaded contexts and rejects
// the create request itself, so the test can check that the upload ran end to end.
const http = require('http')

const port = Number(process.argv[2])
const uploads = []

http
  .createServer((req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${port}`)
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => {
      const body = Buffer.concat(chunks)
      if (url.pathname === '/uploads') {
        res.setHeader('content-type', 'application/json')
        return res.end(JSON.stringify(uploads))
      }
      if (url.pathname.startsWith('/s3/')) {
        if (req.method === 'GET') {
          res.setHeader('content-type', 'application/xml')
          return res.end(
            '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>bucket</Name><KeyCount>0</KeyCount><IsTruncated>false</IsTruncated></ListBucketResult>',
          )
        }
        if (req.method === 'PUT') {
          uploads.push({ key: url.pathname.split('/').slice(3).join('/'), bytes: body.length })
          res.setHeader('etag', '"stub"')
          return res.end()
        }
      }
      res.setHeader('content-type', 'application/json')
      if (url.pathname.endsWith('/object-storage/push-access')) {
        return res.end(
          JSON.stringify({
            storageUrl: `http://127.0.0.1:${port}/s3`,
            accessKey: 'stub',
            secret: 'stub',
            sessionToken: 'stub',
            bucket: 'bucket',
            region: 'us-east-1',
            organizationId: 'org',
          }),
        )
      }
      if (req.method === 'POST' && url.pathname.endsWith('/sandbox')) {
        res.statusCode = 400
        return res.end(JSON.stringify({ statusCode: 400, message: 'stub: create reached' }))
      }
      res.statusCode = 404
      res.end(JSON.stringify({ statusCode: 404, message: `stub: no route for ${req.method} ${url.pathname}` }))
    })
  })
  .listen(port, '127.0.0.1')
