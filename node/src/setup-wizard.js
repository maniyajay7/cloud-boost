/**
 * cloud-boost v3 — setup wizard
 * Guides user through creating a custom Google OAuth Client ID for 5-10x faster uploads.
 */

import { execSync } from 'child_process';
import chalk from 'chalk';

const SEP = '═'.repeat(60);

/**
 * Print the OAuth setup guide.
 */
export function printSetupGuide() {
  console.log(`
${chalk.cyan(SEP)}
  ⚡ cloud-boost — Custom OAuth Client ID Setup
  This is the SINGLE BIGGEST speed improvement you can make.
  Going from shared rclone client ID → your own = 5-10x faster.
${chalk.cyan(SEP)}

${chalk.yellow('Step 1:')} Go to Google Cloud Console
   → https://console.cloud.google.com/

${chalk.yellow('Step 2:')} Create a new project (or use existing)
   → Click "Select Project" → "New Project" → Name it "cloud-boost"

${chalk.yellow('Step 3:')} Enable Google Drive API
   → APIs & Services → Library → Search "Google Drive API" → Enable

${chalk.yellow('Step 4:')} Create OAuth Consent Screen
   → APIs & Services → OAuth consent screen
   → User Type: "External" → Create
   → App name: "cloud-boost" → Your email → Save
   → Scopes: Add "drive" scope → Save
   → Test Users: Add your Gmail address → Save

${chalk.yellow('Step 5:')} Create OAuth Client ID
   → APIs & Services → Credentials → Create Credentials → OAuth Client ID
   → Application type: "Desktop app"
   → Name: "cloud-boost"
   → Download JSON → Save as "client_id.json"

${chalk.yellow('Step 6:')} Configure rclone with YOUR client ID
   → Run: rclone config
   → Edit your Google Drive remote
   → Set client_id and client_secret from your downloaded JSON
   → Re-authorize when prompted

${chalk.green('Done! Your uploads will now be 5-10x faster because you\'re')}
${chalk.green('no longer sharing bandwidth with thousands of other rclone users.')}

${chalk.cyan(SEP)}
`);
}

/**
 * Check if user has a custom client ID configured in rclone.
 * Returns true if custom, false if using shared default.
 */
export function checkCustomClientId() {
  try {
    const result = execSync('rclone config dump', { encoding: 'utf8', timeout: 10000 });
    const config = JSON.parse(result);
    for (const remoteName of Object.keys(config)) {
      const remote = config[remoteName];
      if (remote.type === 'drive') {
        const clientId = remote.client_id || '';
        if (clientId.length > 10) {
          return true; // Has custom client ID
        }
      }
    }
    return false; // Using shared default
  } catch {
    return false;
  }
}
