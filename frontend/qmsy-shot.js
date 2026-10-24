const { chromium } = require('@playwright/test');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 390, height: 844 } });
  await p.goto('http://127.0.0.1:5199/scenic/qingming');
  await p.locator('.qmsy-schedule').scrollIntoViewIfNeeded();
  await p.waitForTimeout(400);
  await p.screenshot({ path: '/tmp/qmsy-schedule-mobile.png' });
  const d = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await d.goto('http://127.0.0.1:5199/scenic/qingming');
  await d.locator('.qmsy-schedule').scrollIntoViewIfNeeded();
  await d.waitForTimeout(400);
  await d.screenshot({ path: '/tmp/qmsy-schedule-desktop.png' });
  await b.close();
})();
