// Reference demo scenario for `wt-browser.sh <slug> record <scenario.js> <KEY>`. Copy it to
// .worktrees/_env/<slug>/browser/<KEY>_demo.js and replace the steps.
//
// One function expression, called with the session's page and `demo` (demo-helpers.js).
// It runs in playwright-cli's run-code sandbox: no require, no process, no files.
// - The session is open and logged in as the role's user before record runs; the scenario
//   never logs in, so no password is written here.
// - It prepares its own starting data before demo.start(), through the UI or page.request
//   to the session's RPC: a second take needs nothing an earlier one left.
// - Navigate through menus and clicks: page.goto() with only a new #hash does not route the
//   web client.
// - Locators are the ones the validation run used (roles, titles, labels; `generate-locator`
//   prints one for a snapshot ref). Wait for what the next step reads, not for a timeout.
// - Captions are plain English for the people who asked for the change: what they see and
//   why it matters, no model, field or module names.
async (page, demo) => {
  await page.goto(demo.url + '/web');
  await page.getByTitle('Home Menu').waitFor();

  await demo.start();
  await demo.title('Sales orders at a glance', 'Sales Manager');

  await demo.step('Open the Sales app from the home menu', async () => {
    await page.getByTitle('Home Menu').click();
    await page.getByRole('menuitem', { name: 'Sales', exact: true }).click();
    await page.locator('.o_list_view .o_data_row').first().waitFor();
  });

  await demo.step('The list shows every quotation and order', null, 2000);

  await demo.step('Open an order to see its details', async () => {
    await page.locator('.o_list_view .o_data_row .o_data_cell').first().click();
    await page.locator('.o_form_view .o_statusbar_status').waitFor();
  });

  const caption = await demo.caption('The status bar shows where the order stands');
  await demo.callout(page.locator('.o_statusbar_status'), 'Order status', 2500);
  await page.waitForTimeout(2600);
  await caption.dispose();
}
