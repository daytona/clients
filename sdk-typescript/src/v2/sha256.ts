/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// An incremental SHA-256 (FIPS 180-4) for runtimes without node:crypto. Web
// Crypto only digests a whole buffer, which for a 1 GiB build context would be
// a second 1 GiB allocation.

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

const rotr = (x: number, n: number): number => (x >>> n) | (x << (32 - n))

export class Sha256 {
  private readonly state = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19])
  private readonly block = new Uint8Array(64)
  private readonly words = new Uint32Array(64)
  private buffered = 0
  private length = 0

  public update(data: Uint8Array): this {
    this.length += data.byteLength
    let offset = 0
    if (this.buffered > 0) {
      const take = Math.min(64 - this.buffered, data.byteLength)
      this.block.set(data.subarray(0, take), this.buffered)
      this.buffered += take
      offset = take
      if (this.buffered < 64) return this
      this.compress(this.block, 0)
      this.buffered = 0
    }
    for (; offset + 64 <= data.byteLength; offset += 64) this.compress(data, offset)
    if (offset < data.byteLength) {
      this.block.set(data.subarray(offset))
      this.buffered = data.byteLength - offset
    }
    return this
  }

  public digestHex(): string {
    const bits = this.length * 8
    const padding = new Uint8Array((this.buffered < 56 ? 56 : 120) - this.buffered + 8)
    padding[0] = 0x80
    const view = new DataView(padding.buffer)
    view.setUint32(padding.byteLength - 8, Math.floor(bits / 0x1_0000_0000))
    view.setUint32(padding.byteLength - 4, bits >>> 0)
    this.update(padding)
    return Array.from(this.state, (word) => word.toString(16).padStart(8, '0')).join('')
  }

  private compress(data: Uint8Array, offset: number): void {
    const w = this.words
    for (let i = 0; i < 16; i++) {
      const at = offset + i * 4
      w[i] = (data[at] << 24) | (data[at + 1] << 16) | (data[at + 2] << 8) | data[at + 3]
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = w[i - 16] + s0 + w[i - 7] + s1
    }
    let [a, b, c, d, e, f, g, h] = this.state
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0
      h = g
      g = f
      f = e
      e = (d + t1) | 0
      d = c
      c = b
      b = a
      a = (t1 + t2) | 0
    }
    const s = this.state
    s[0] += a
    s[1] += b
    s[2] += c
    s[3] += d
    s[4] += e
    s[5] += f
    s[6] += g
    s[7] += h
  }
}
