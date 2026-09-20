// Guard for the PUBLIC repository. Run it before every push (`npm run check:public`; a local pre-push hook does it too).
// It fails when a file that is (or is about to be) tracked breaks the rules in CLAUDE.md, section "Aturan repo publik".
// It only reads files. It never prints the matched secret, only where it is and which rule it broke.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const listed = execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
const files = listed.split('\0').filter(Boolean).filter((f) => fs.existsSync(f)); // a file removed from disk but still staged is being deleted, fine

// 1. Files that must never be in the repository, whatever they contain.
const FORBIDDEN_PATH = [
  [/(^|\/)\.env(\..+)?$/i, 'environment file (only .env.example is allowed)', (f) => !/(^|\/)\.env\.example$/i.test(f)],
  // Schema/migration .sql files are source code and belong in the repo; only dumps/backups are forbidden.
  [/\.(sql|dump|bak|sqlite3?|db|zip|7z|rar|tar|gz|pem|key|p12|pfx|jks)$/i, 'database dump, backup, archive or key file', (f) => !(/\.sql$/i.test(f) && /(^|\/)(migrations|supabase)\/|(^|\/)schema\.sql$/i.test(f))],
  [/(^|\/)(backups|deploy|data|\.claude)\//i, 'local-only folder (backups, deploy, data, .claude)'],
  [/(^|\/)(node_modules|\.next|out|dist|coverage)\//i, 'generated or installed folder'],
  [/cuplikan layar|screenshot \d|whatsapp image|^img_\d|^dsc\d/i, 'raw screenshot or chat/camera photo (rename it and put it in docs/screenshots if it is meant to be shown)'],
];

// 2. Things that must not appear inside a file.
// Documented examples: the run of digits 3456 7890 (and 1234567890) never belongs to a real number here.
const FAKE_PHONES = ['6281234567890', '081234567890', '34567890'];
const OK_EMAIL_DOMAIN = /@(example\.(com|org|net)|localhost|users\.noreply\.github\.com|noreply\.github\.com|noreply\.anthropic\.com|resend\.dev|contoh\.(com|co\.id))$/i;
// Optional per-repo `.check-public-allow`: one path regex per line (# comments ok). Files matching skip the CONTENT checks only
// (never the forbidden-path checks). Use it for contact details the owner shows on purpose, e.g. their own portfolio site.
const ALLOW = fs.existsSync('.check-public-allow')
  ? fs.readFileSync('.check-public-allow', 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((l) => new RegExp(l))
  : [];
const PLACEHOLDER_PASSWORD = /^(PASSWORD|DB_PASSWORD|PASS|password|pass|\*+|\.{3}|change-?me.*|<.*>|%.*|YOUR.*|xxx+)$/i;

const CONTENT = [
  ['private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ['access token', /\b(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})/],
  ['Hostinger account id in a database name or user (u + 9 digits + underscore)', /\bu\d{9}_[A-Za-z0-9]/],
  ['a personal Windows path (C:\\Users\\<name>)', /[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+/],
];

const SKIP_CONTENT = /(^|\/)(package-lock\.json|scripts\/check-public\.mjs)$|\.(png|jpe?g|webp|gif|ico|woff2?|ttf|pdf)$/i;

const problems = [];
const add = (file, what) => problems.push(`  ${file}\n      ${what}`);

for (const f of files) {
  for (const [re, why, extra] of FORBIDDEN_PATH) {
    if (re.test(f) && (!extra || extra(f))) add(f, `not allowed: ${why}`);
  }
  if (SKIP_CONTENT.test(f) || ALLOW.some((re) => re.test(f))) continue;
  let text;
  try {
    if (fs.statSync(f).size > 2_000_000) continue;
    text = fs.readFileSync(f, 'utf8');
  } catch { continue; }
  if (text.includes('\0')) continue; // binary

  for (const [name, re] of CONTENT) if (re.test(text)) add(f, `contains ${name}`);

  // a database URL with a real-looking password
  for (const m of text.matchAll(/(mysql|mariadb|postgres(?:ql)?):\/\/([^:\s/@]+):([^@\s]+)@/gi)) {
    if (!PLACEHOLDER_PASSWORD.test(m[3])) add(f, 'contains a database URL with a password that is not an obvious placeholder');
  }
  // real e-mail addresses (placeholders on example.com are fine)
  for (const m of text.matchAll(/[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g)) {
    if (OK_EMAIL_DOMAIN.test(m[0]) || /^git@/i.test(m[0])) continue;
    add(f, 'contains an e-mail address that is not on example.com');
    break;
  }
  // Indonesian mobile numbers (+62 8xx, 62 8xx, 08xx) that are not the documented fake example
  for (const m of text.matchAll(/(?:\+?62[\s-]?|\b0)8\d{1,2}[\s-]?\d{3,4}[\s-]?\d{3,5}\b/g)) {
    if (FAKE_PHONES.some((p) => m[0].replace(/\s+/g, '').includes(p.replace(/\s+/g, '')))) continue;
    add(f, 'contains something that looks like a real phone number');
    break;
  }
}

if (problems.length) {
  console.error(`check-public: ${problems.length} problem(s) found. Fix them, or add the file to .gitignore, before pushing.\n`);
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(`check-public: OK (${files.length} files checked, nothing that should stay private)`);
