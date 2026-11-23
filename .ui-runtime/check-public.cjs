const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.UI_PLAYWRIGHT_MODULE);
let browser;
(async()=>{
  const output=process.env.UI_OUTPUT_DIR;
  fs.mkdirSync(output,{recursive:true});
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const context=await browser.newContext();
  await context.route('https://**/*',route=>route.abort());
  const page=await context.newPage();
  const pages=[['/','Home'],['/sas/','Self-assessment'],['/resend/','Find my passport'],['/accounts/login/',null]];
  const results=[];
  for(const width of [320,390,768,1024,1440]){
    await page.setViewportSize({width,height:900});
    for(const [url,active] of pages){
      const response=await page.goto('http://127.0.0.1:8000'+url);
      assert.equal(response.status(),200);
      await page.waitForFunction(()=>document.body.classList.contains('sidebar-enhanced'));
      await page.locator('#app-sidebar').waitFor({state:width>=1024?'visible':'hidden'});
      const labels=await page.locator('#app-sidebar nav a').allTextContents();
      assert.deepEqual(labels.map(x=>x.trim()),['Home','Self-assessment','Find my passport']);
      assert.equal(await page.locator('#app-sidebar [aria-current="page"]').count(),active?1:0);
      if(active)assert.equal((await page.locator('#app-sidebar [aria-current="page"]').textContent()).trim(),active);
      assert.equal(await page.locator('#app-sidebar .sidebar-brand a').getAttribute('href'),'/');
      const geometry=await page.evaluate(()=>({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,left:document.getElementById('app-body').getBoundingClientRect().left}));
      assert.ok(geometry.scrollWidth<=width+1,url+' overflows at '+width);
      assert.equal(geometry.left,width>=1024?240:0);
      results.push({url,viewport:width,...geometry});
      if(url==='/resend/' && [390,1440].includes(width))await page.screenshot({path:path.join(output,'public-sidebar-'+width+'.png'),fullPage:true});
      if(width<1024){
        const toggle=page.locator('#sidebar-toggle');
        await toggle.click();
        assert.equal(await toggle.getAttribute('aria-expanded'),'true');
        assert.equal(await page.locator('#app-body').evaluate(el=>el.inert),true);
        if(active)assert.equal(await page.evaluate(()=>document.activeElement.textContent.trim()),active);
        if(url==='/resend/' && width===390)await page.screenshot({path:path.join(output,'public-mobile-menu.png')});
        await page.keyboard.press('Escape');
        assert.equal(await toggle.getAttribute('aria-expanded'),'false');
        assert.equal(await toggle.evaluate(el=>el===document.activeElement),true);
        assert.equal(await page.locator('#app-body').evaluate(el=>el.inert),false);
      }
    }
  }
  await page.goto('http://127.0.0.1:8000/static/.ui-preview-hub.html');
  const signedIn=await page.locator('#app-sidebar nav a').allTextContents();
  assert.deepEqual(signedIn.map(x=>x.trim()),['Overview','My passport','Sessions','Credential reviews','Reports','Competency library']);
  fs.writeFileSync(path.join(output,'public-sidebar-results.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({layoutChecks:results.length,publicLinks:'passed',activePages:'passed',mobileMenu:'passed',signedInNavigation:'passed'}));
  await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exitCode=1;});
