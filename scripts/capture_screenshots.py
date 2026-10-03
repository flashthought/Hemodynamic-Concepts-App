"""
UI test + storyboard screenshots (BME Design 6.6.1). Needs the app running:
    scripts/run_app.sh --port 8765 &   then   python scripts/capture_screenshots.py [base_url]
Checks: every page renders, no JavaScript errors, no horizontal overflow at phone width.
Writes docs/figures/ui0*.png.
"""
import os
import asyncio
from playwright.async_api import async_playwright
import sys
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
OUT = os.environ.get("SHOTS_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "figures")) + "/"
async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        await pg.goto(BASE + "#/"); await pg.wait_for_selector(".concept-card"); await pg.wait_for_timeout(600)
        await pg.screenshot(path=OUT + "ui01_overview.png", full_page=True)
        for c, rec, name in (("a", "synthetic_4993", "ui02_concept_a.png"), ("b", "synthetic_4993", "ui03_concept_b.png")):
            await pg.goto(BASE + f"#/concept-{c}"); await pg.wait_for_selector("#rec-select option", state="attached")
            await pg.select_option("#rec-select", rec); await pg.wait_for_timeout(800)
            await pg.click('button[data-speed="20"]'); await pg.click("#play-btn")
            await pg.wait_for_timeout(9000); await pg.click("#play-btn"); await pg.wait_for_timeout(600)
            await pg.screenshot(path=OUT + name, full_page=True)
        # Concept A, vasopressor case with intervention marked in data
        await pg.goto(BASE + "#/concept-a"); await pg.wait_for_selector("#rec-select option", state="attached")
        await pg.select_option("#rec-select", "synthetic_668"); await pg.wait_for_timeout(800)
        await pg.click('button[data-speed="20"]'); await pg.click("#play-btn")
        await pg.wait_for_timeout(7500); await pg.click("#play-btn"); await pg.wait_for_timeout(600)
        await pg.screenshot(path=OUT + "ui04_concept_a_intervention.png", full_page=True)
        await pg.goto(BASE + "#/concept-c"); await pg.wait_for_timeout(1200)
        sel = pg.locator("select").first
        await sel.select_option("vitaldb_6131")
        await pg.get_by_text("Generate review").click()
        await pg.wait_for_selector("text=Open printable report", timeout=120000); await pg.wait_for_timeout(800)
        await pg.screenshot(path=OUT + "ui05_concept_c.png", full_page=True)
        await pg.goto(BASE + "#/evidence"); await pg.wait_for_timeout(1500)
        await pg.screenshot(path=OUT + "ui06_evidence.png", full_page=True)
        await pg.goto(BASE + "#/design/04_concept_selection"); await pg.wait_for_selector("#doc-body h1"); await pg.wait_for_timeout(1500)
        # Images are lazy-loaded: force every one to load before checking it.
        await pg.evaluate("document.querySelectorAll('#doc-body img').forEach(i => i.loading = 'eager')")
        await pg.evaluate("Promise.all([...document.querySelectorAll('#doc-body img')].map(i => i.decode().catch(() => null)))")
        broken = await pg.evaluate("[...document.querySelectorAll('#doc-body img')].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.src)")
        links = await pg.evaluate("[...document.querySelectorAll('.doc-nav a')].length")
        print("design docs:", links, "broken images:", broken)
        errors += [f"broken image {u}" for u in broken]
        await pg.evaluate("window.scrollTo(0, 0)")
        await pg.screenshot(path=OUT + "ui07_design_file.png")
        m = await b.new_page(viewport={"width": 390, "height": 844})
        await m.goto(BASE + "#/concept-a"); await m.wait_for_timeout(1500)
        ow = await m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        print("mobile overflow:", ow)
        await b.close()
    print("JS errors:", errors)
    if errors or ow:
        sys.exit(1)
asyncio.run(main())
