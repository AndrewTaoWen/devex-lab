// Open the map page in a headless browser, switch to a Graph view, and report what was drawn and any errors.
// Usage: node page_check.js URL VIEW   (VIEW: overview)
const {chromium} = require("playwright");
(async () => {
  const [url, view] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 900}});
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(url);
  await page.click('[data-view="graph"]');
  await page.click(`[data-gview="${view}"]`);
  await page.waitForSelector(".rc", {timeout: 20000});
  const report = {errors, cells: await page.$$eval(".rc", els => els.length), landmarks: await page.$$eval(".rl", els => els.length)};
  console.log(JSON.stringify(report));
  await browser.close();
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
