import { spawn } from 'child_process';
import { exec } from 'child_process';

const url = 'http://localhost:4173';

const server = spawn('pnpm', ['run', 'preview'], { stdio: 'ignore' });

setTimeout(() => {
  exec(`npx lighthouse ${url} --chrome-flags="--headless" --only-categories=pwa --output=json`, (err, stdout, stderr) => {
    if (err) {
      console.error(err);
      server.kill();
      process.exit(1);
    }
    const result = JSON.parse(stdout);
    const pwaScore = result.categories.pwa.score;
    console.log(`Lighthouse PWA Score: ${pwaScore * 100}`);
    server.kill();
    process.exit(pwaScore < 1 ? 1 : 0);
  });
}, 5000);
