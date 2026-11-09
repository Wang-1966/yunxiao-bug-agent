// Run against Vite: NODE_PATH=<playwright node_modules> node tests/llm-config.cjs
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
    page.setDefaultTimeout(10000);
    const providers = [
      { id: 'a', name: 'Provider A', default_model: 'a-default', available_models: ['a-default', 'a-other'] },
      { id: 'b', name: 'Provider B', default_model: 'b-default', available_models: ['b-other'] },
      { id: 'c', name: 'Provider C', default_model: '', available_models: ['c-first'] },
    ].map((provider) => ({ ...provider, type: 'openai-compatible', base_url: 'https://example.com/v1', api_key: '', enabled: true }));
    let config = {
      providers, default_provider: 'a', default_model: 'legacy-custom', use_stage_config: true,
      response_style: 'detailed',
      stage_config: Object.fromEntries(['analyze', 'propose', 'fix', 'verify'].map((stage) => [stage, {
        provider: 'a', model: 'a-other', temperature: 0.2, max_tokens: 8000,
      }])),
    };
    let saved;
    await page.route('**/api/**', async (route) => {
      if (new URL(route.request().url()).pathname === '/api/llm-config') {
        if (route.request().method() === 'PUT') {
          saved = route.request().postDataJSON();
          config = { ...config, ...saved };
        }
        await route.fulfill({ json: config });
      } else {
        await route.fulfill({ json: {} });
      }
    });
    await page.goto(process.env.TEST_URL || 'http://localhost:3001/llm-config');
    const defaultModel = page.locator('#default_model');
    await defaultModel.waitFor();
    assert.equal(await defaultModel.inputValue(), 'legacy-custom');

    async function chooseProvider(selector, name) {
      await page.locator('.ant-select').filter({ has: page.locator(selector) }).click();
      await page.locator('.ant-select-dropdown').filter({ has: page.locator(`${selector}_list`) })
        .locator('.ant-select-item-option-content').getByText(name, { exact: true }).click();
    }
    async function chooseModel(input, name) {
      await input.fill('');
      await input.click();
      await page.locator('.ant-select-dropdown').filter({ has: page.locator(`#${await input.getAttribute('id')}_list`) })
        .locator('.ant-select-item-option-content').getByText(name, { exact: true }).click();
      assert.equal(await input.inputValue(), name);
    }

    await chooseProvider('#default_provider', 'Provider B');
    assert.equal(await defaultModel.inputValue(), 'b-default');
    await chooseModel(defaultModel, 'b-other');
    for (const stage of ['analyze', 'propose', 'fix', 'verify']) {
      const input = page.locator(`#stage_config_${stage}_model`);
      await chooseProvider(`#stage_config_${stage}_provider`, 'Provider B');
      assert.equal(await input.inputValue(), 'b-default');
      await input.fill('B-OTH');
      await page.locator('.ant-select-dropdown').filter({ has: page.locator(`#stage_config_${stage}_model_list`) })
        .locator('.ant-select-item-option-content').getByText('b-other', { exact: true }).click();
      assert.equal(await input.inputValue(), 'b-other');
    }
    await chooseProvider('#stage_config_analyze_provider', 'Provider C');
    assert.equal(await page.locator('#stage_config_analyze_model').inputValue(), 'c-first');
    await defaultModel.fill('custom-model');
    await page.locator('button[type="submit"]').filter({ hasText: '保存配置' }).click();
    await page.getByText('配置保存成功', { exact: true }).waitFor();
    assert.equal(saved.default_model, 'custom-model');
    assert.equal(saved.default_provider, 'b');
    assert.equal(saved.stage_config.analyze.model, 'c-first');
    assert.equal(saved.stage_config.fix.temperature, 0.2);
    assert.equal(saved.stage_config.fix.max_tokens, 8000);
    await page.reload();
    await defaultModel.waitFor();
    assert.equal(await defaultModel.inputValue(), 'custom-model');
    console.log('LLM config: provider/model linking, choices, search, custom values, save/reload passed');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
