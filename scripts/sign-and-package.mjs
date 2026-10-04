// Personal packaging only: keep the admitted official checkout unchanged.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { pathToFileURL } from 'node:url'

export function signingOptions(source, app) {
  const entitlements = path.join(source, 'apps/desktop/electron')
  return {
    app, platform: 'darwin', type: 'distribution', identity: '-',
    identityValidation: false, batchCodesignCalls: false,
    preAutoEntitlements: false,
    optionsForFile: file => ({
      hardenedRuntime: true,
      entitlements: path.join(entitlements, file === app
        ? 'entitlements.mac.plist' : 'entitlements.mac.inherit.plist')
    })
  }
}

function verifyAdhoc(app) {
  execFileSync('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], { stdio: 'inherit' })
}

export const packageNames = ['Hermes-Light-arm64.dmg', 'Hermes-Light-arm64.zip']

function nativeApp(source) {
  const appDir = path.join(source, 'apps/desktop/release/mac-arm64')
  const apps = fs.existsSync(appDir) ? fs.readdirSync(appDir).filter(name => name.endsWith('.app')) : []
  assert.equal(apps.length, 1, 'Expected one ARM64 app from the official native build')
  const app = path.join(appDir, apps[0])
  assert(fs.lstatSync(app).isDirectory(), 'Expected a real application directory')
  return app
}

function isolatedPath(source, target) {
  target = path.join(fs.realpathSync(path.dirname(path.resolve(target))), path.basename(target))
  assert(!target.startsWith(source + path.sep) && target !== source, 'Final packages must be outside official source')
  return target
}

function sha256(file) {
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

export function verifyApp(app, commit, original) {
  assert(fs.lstatSync(app).isDirectory(), 'Expected a real application directory')
  const resources = path.join(app, 'Contents/Resources')
  const stamp = JSON.parse(fs.readFileSync(path.join(resources, 'install-stamp.json'), 'utf8'))
  assert.equal(stamp.payload, 'light', 'Not a Light build')
  assert.equal(stamp.commit, commit, 'Source identity mismatch')
  assert.equal(stamp.updateMechanism, 'external', 'Personal builds must use external updates')
  assert(!fs.readdirSync(resources).includes('agent-payload'), 'Unexpected local backend payload')
  const info = JSON.parse(execFileSync('plutil', ['-convert', 'json', '-o', '-', path.join(app, 'Contents/Info.plist')], { encoding: 'utf8' }))
  const name = info.CFBundleExecutable
  assert(typeof name === 'string' && name !== '.' && name !== '..' && path.basename(name) === name, 'Invalid executable path')
  const executable = path.join(app, 'Contents/MacOS', name)
  assert(fs.lstatSync(executable).isFile(), 'Expected a real executable file')
  const arches = execFileSync('lipo', ['-archs', executable], { encoding: 'utf8' }).trim().split(/\s+/)
  assert.deepEqual(arches, ['arm64'], 'Unexpected architectures')
  verifyAdhoc(app)
  const signature = spawnSync('codesign', ['--display', '--verbose=4', app], { encoding: 'utf8' })
  assert.equal(signature.status, 0, signature.stderr)
  assert(signature.stderr.split(/\r?\n/).includes('Signature=adhoc'), 'Expected personal ad-hoc signature')
  const executable_sha256 = sha256(executable)
  if (original) {
    assert.deepEqual(stamp, original.stamp, 'Distributed stamp mismatch')
    assert.equal(executable_sha256, original.executable_sha256, 'Distributed executable mismatch')
  }
  console.log(`Verified ${app}: arm64, Signature=adhoc`)
  return { app, executable, stamp, executable_sha256 }
}

export function verifyPackages(source, output, commit, artifacts) {
  source = fs.realpathSync(source)
  output = isolatedPath(source, output)
  assert(fs.lstatSync(output).isDirectory(), 'Expected a real isolated package directory')
  assert.deepEqual(fs.readdirSync(output).sort(), [...packageNames, 'packages.json'].sort(), 'Unexpected final package files')
  for (const name of [...packageNames, 'packages.json']) {
    const stat = fs.lstatSync(path.join(output, name))
    assert(stat.isFile() && stat.size > 0, 'Invalid package path or empty file')
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(output, 'packages.json'), 'utf8'))
  assert.deepEqual(manifest, { packages: packageNames }, 'Invalid package manifest')
  const original = verifyApp(nativeApp(source), commit)
  artifacts = isolatedPath(source, artifacts)
  fs.mkdirSync(artifacts) // Never reuse stale publication files.
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-light-verify-'))
  try {
    for (const name of packageNames) {
      const target = path.join(artifacts, name)
      fs.copyFileSync(path.join(output, name), target, fs.constants.COPYFILE_EXCL)
      assert.equal(sha256(target), sha256(path.join(output, name)), 'Package copy changed')
      const unpacked = path.join(scratch, name.endsWith('.zip') ? 'zip' : 'dmg')
      fs.mkdirSync(unpacked)
      if (name.endsWith('.zip')) {
        execFileSync('ditto', ['-x', '-k', target, unpacked], { stdio: 'inherit' })
        assert.deepEqual(fs.readdirSync(unpacked), [path.basename(original.app)], 'Unexpected installer contents')
        verifyApp(path.join(unpacked, path.basename(original.app)), commit, original)
      } else {
        execFileSync('hdiutil', ['attach', '-readonly', '-nobrowse', '-mountpoint', unpacked, target], { stdio: 'inherit' })
        try {
          assert.deepEqual(fs.readdirSync(unpacked).sort(), ['Applications', path.basename(original.app)].sort(), 'Unexpected installer contents')
          assert(fs.lstatSync(path.join(unpacked, 'Applications')).isSymbolicLink(), 'Invalid Applications link')
          assert.equal(fs.readlinkSync(path.join(unpacked, 'Applications')), '/Applications', 'Invalid Applications link')
          verifyApp(path.join(unpacked, path.basename(original.app)), commit, original)
        } finally {
          execFileSync('hdiutil', ['detach', unpacked], { stdio: 'inherit' })
        }
      }
    }
    const sums = packageNames.map(name => `${sha256(path.join(artifacts, name))}  ${name}`)
    fs.writeFileSync(path.join(artifacts, 'SHA256SUMS.txt'), sums.join('\n') + '\n')
    const report = { source_repository: 'NousResearch/hermes-agent', commit,
      target: 'darwin-arm64', payload: 'light', signature: 'ad-hoc', apple_notarized: false,
      codesign_verified: true, backend_payload_absent: true, distributed_copies_verified: true,
      executable_sha256: original.executable_sha256, verified_packages: packageNames,
      installation_on_M5_verified: false }
    fs.writeFileSync(path.join(artifacts, 'BUILD-INFO.json'), JSON.stringify(report, null, 2) + '\n')
    if (process.env.GITHUB_ENV) {
      fs.appendFileSync(process.env.GITHUB_ENV, `LIGHT_APP=${original.app}\nLIGHT_EXECUTABLE=${original.executable}\n`)
    }
    console.log(JSON.stringify(report, null, 2))
    return report
  } catch (error) {
    fs.rmSync(artifacts, { recursive: true, force: true })
    throw error
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true })
  }
}

export async function signAndPackage(source, output) {
  source = fs.realpathSync(source)
  const app = nativeApp(source)
  const resources = path.join(app, 'Contents/Resources')
  const stamp = JSON.parse(fs.readFileSync(path.join(resources, 'install-stamp.json'), 'utf8'))
  assert.equal(stamp.payload, 'light', 'Refusing to sign a non-Light application')
  assert.equal(fs.existsSync(path.join(resources, 'agent-payload')), false, 'Unexpected local backend payload')
  output = isolatedPath(source, output)
  fs.mkdirSync(output) // Exclusive: refuse stale output, including symlinks.
  const require = createRequire(path.join(source, 'apps/desktop/package.json'))
  const { sign } = require('@electron/osx-sign')
  await sign(signingOptions(source, app))
  verifyAdhoc(app)

  const staging = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-light-dmg-'))
  try {
    execFileSync('ditto', [app, path.join(staging, path.basename(app))], { stdio: 'inherit' })
    fs.symlinkSync('/Applications', path.join(staging, 'Applications'))
    execFileSync('hdiutil', ['create', '-ov', '-format', 'UDZO', '-volname', path.basename(app, '.app'),
      '-srcfolder', staging, path.join(output, packageNames[0])], { stdio: 'inherit' })
    execFileSync('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', app, path.join(output, packageNames[1])], { stdio: 'inherit' })
  } finally {
    fs.rmSync(staging, { recursive: true, force: true })
  }
  fs.writeFileSync(path.join(output, 'packages.json'), JSON.stringify({ packages: packageNames }) + '\n')
  console.log(`Personal ad-hoc packages (not yet distribution-verified): ${output}`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  if (process.argv[2] === '--verify') {
    assert.equal(process.argv.length, 7, 'Usage: node sign-and-package.mjs --verify SOURCE OUTPUT COMMIT ARTIFACTS')
    verifyPackages(...process.argv.slice(3))
  } else {
    assert.equal(process.argv.length, 4, 'Usage: node sign-and-package.mjs SOURCE OUTPUT')
    await signAndPackage(process.argv[2], process.argv[3])
  }
}
