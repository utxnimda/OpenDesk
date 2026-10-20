import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkVersions, prepareRelease, readReleaseDraft, readVersionManifest, releaseTags, syncVersions } from './versioning.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [command, ...args] = process.argv.slice(2);
try {
  let result;
  if (command === 'prepare' && args.length === 2 && args[0] === '--file') {
    result = prepareRelease(root, readReleaseDraft(args[1]));
  } else if (args.length === 0 && command === 'check') {
    result = checkVersions(root);
  } else if (args.length === 0 && command === 'sync') {
    result = syncVersions(root);
  } else if (args.length === 0 && command === 'tags') {
    console.log(releaseTags(readVersionManifest(root)).join('\n'));
  } else {
    throw new Error('用法：node scripts/version.mjs check|sync|tags 或 prepare --file <release-draft.json>');
  }
  if (result) console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error(`版本操作失败：${error.message}`);
  process.exitCode = 1;
}
