// The `demo` object a scenario gets from `wt-browser.sh <slug> record`: the recording in the
// demo format, and the title card, caption and callout overlays. One function expression:
// record calls it with the page and { key, url, video } and passes the result on.
(page, env) => {
  const sans = 'font-family: system-ui, -apple-system, "Segoe UI", sans-serif';
  let recording = false;
  const demo = {
    key: env.key,
    // The env's origin: page.goto(demo.url + '/web')
    url: env.url,

    // Starts the recording: 1280x720 at 25 fps, with the cursor and a mark where it clicks,
    // each action held for 800 ms. Call it once the first screen has loaded.
    async start() {
      await page.screencast.start({ path: env.video, size: { width: 1280, height: 720 }, fps: 25 });
      recording = true;
      await page.screencast.showActions({
        duration: 800,
        style: {
          point: 'width: 22px; height: 22px; border-radius: 50%; background: rgba(230, 57, 70, .75)',
          title: 'display: none',
        },
      });
    },

    // Stops the recording; record calls it after the scenario, whatever it did.
    async stop() {
      if (!recording)
        return;
      recording = false;
      await page.screencast.stop();
    },

    // The title card: the task key and title, and the role the screen is shown as. It opens
    // the demo, and each part of a flow across two roles.
    async title(title, role, ms = 3000) {
      await page.screencast.showChapter(`${env.key} ${title}`, {
        description: `Shown as: ${role} (test copy)`,
        duration: ms,
      });
    },

    // A caption at the bottom of the frame, until its dispose().
    async caption(text) {
      return page.screencast.showOverlay(`
        <div style="position: absolute; bottom: 24px; left: 50%; transform: translateX(-50%);
          max-width: 80%; padding: 12px 32px; background: rgba(0, 0, 0, .85); color: #fff;
          border-radius: 8px; font-size: 22px; font-weight: 600; ${sans}; text-align: center;
          box-shadow: 0 4px 12px rgba(0, 0, 0, .3);">${text}</div>`);
    },

    // A frame around an element with a label under it, for ms.
    async callout(locator, text, ms = 2500) {
      const box = await locator.boundingBox();
      return page.screencast.showOverlay(`
        <div style="position: absolute; top: ${box.y - 4}px; left: ${box.x - 4}px;
          width: ${box.width + 8}px; height: ${box.height + 8}px;
          outline: 3px solid #e63946; border-radius: 6px;"></div>
        <div style="position: absolute; top: ${box.y + box.height + 14}px;
          left: ${box.x + box.width / 2}px; transform: translateX(-50%); padding: 6px 14px;
          background: #e63946; color: #fff; border-radius: 6px; font-size: 16px;
          font-weight: 600; ${sans}; white-space: nowrap;">${text}</div>`, { duration: ms });
    },

    // One step: the caption stays while the action runs and for ms after it.
    async step(text, action, ms = 1500) {
      const caption = await demo.caption(text);
      if (action)
        await action();
      await page.waitForTimeout(ms);
      await caption.dispose();
    },
  };
  return demo;
}
