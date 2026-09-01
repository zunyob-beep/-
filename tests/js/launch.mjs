// 크로미움을 어디서 찾을지.
//
// 이 개발 환경에는 미리 깔려 있어서 받으러 나가면 안 되고(막혀 있다),
// 깃허브 CI에서는 playwright가 자기 자리에 받아 둔다. 자리가 다르므로
// 있는 쪽을 쓴다 — 한 곳에 박아 두면 다른 쪽에서 못 돈다.

import { existsSync } from 'node:fs';

const PREINSTALLED = '/opt/pw-browsers/chromium';

export const launchOptions = existsSync(PREINSTALLED)
  ? { executablePath: PREINSTALLED }
  : {};
