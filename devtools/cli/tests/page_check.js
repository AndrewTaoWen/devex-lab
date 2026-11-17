// Open the map page in a headless browser, switch to a Graph view, and report what was drawn and any errors.
// Usage: node page_check.js URL VIEW [FUNCTION_KEY]
//   overview       cells and landmarks drawn
//   neighborhood   after clicking FUNCTION_KEY: how many cells are lit as callers and callees, and the links drawn
//   coupling       cell positions under the folder layout and under the coupling layout
const {chromium} = require("playwright");
(async () => {
  const [url, view, key] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 900}});
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(url);
  await page.click('[data-view="graph"]');
  await page.click(`[data-gview="${view === "coupling" ? "overview" : view}"]`);
  await page.waitForSelector(".rc", {timeout: 20000});
  const report = {errors, cells: await page.$$eval(".rc", els => els.length), landmarks: await page.$$eval(".rl", els => els.length)};
  if (view === "neighborhood") {
    await page.click(`.rc[aria-label="${key}"]`);
    await page.waitForTimeout(200);
    report.callers = await page.$$eval(".rc.in", els => els.map(e => e.getAttribute("aria-label")).sort());
    report.callees = await page.$$eval(".rc.out", els => els.map(e => e.getAttribute("aria-label")).sort());
    report.links = await page.$$eval(".rlk", els => els.length);
  }
  if (view === "coupling") {
    const where = () => page.$$eval(".rc", els => els.map(e => [e.getAttribute("aria-label"), e.getAttribute("x"), e.getAttribute("y")]));
    report.folders = await where();
    await page.selectOption("#rglayout", "coupling");
    await page.waitForTimeout(900);
    report.coupling = await where();
  }
  console.log(JSON.stringify(report));
  await browser.close();
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
