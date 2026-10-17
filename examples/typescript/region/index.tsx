import { Daytona } from '@daytona/sdk'
import { Image } from '@daytona/sdk/v1'

async function main() {
  const daytona = new Daytona()

  const snapshot1 = `us-${Date.now()}`
  console.log(`Creating snapshot ${snapshot1}`)
  try {
    await daytona.v1.snapshot.create({
      name: snapshot1,
      image: Image.debianSlim('3.12'),
      regionId: 'us',
    })
  } catch (error: any) {
    console.error(error?.message)
  }
  console.log('--------------------------------')

  const snapshot2 = `eu-${Date.now()}`
  console.log(`Creating snapshot ${snapshot2}`)
  try {
    await daytona.v1.snapshot.create({
      name: snapshot2,
      image: Image.debianSlim('3.13'),
      regionId: 'eu',
    })
  } catch (error: any) {
    console.error('error', error?.message)
  }
  console.log('--------------------------------')

  console.log(`Creating sandbox from snapshot ${snapshot1}`)
  try {
    const sandbox = await daytona.v1.create({
      snapshot: snapshot1,
      target: 'us',
    })
    await daytona.v1.delete(sandbox)
  } catch (error: any) {
    console.error(error?.message)
  }
  console.log('--------------------------------')

  console.log(`Creating sandbox from snapshot ${snapshot2}`)
  try {
    const sandbox = await daytona.v1.create({
      snapshot: snapshot2,
      target: 'eu',
    })
    await daytona.v1.delete(sandbox)
  } catch (error: any) {
    console.error('error', error?.message)
  }
}

main().catch(console.error)
