# Free cloud deployment (Render + Neon) — no data loss, no card

Render's free web service has an **ephemeral disk**: everything written next to the app
(SQLite DB, face photos, trained model, payslips, backups) is deleted on every restart,
redeploy and 15-minute sleep. This setup keeps **all of it inside a free Neon Postgres
database** instead, using `storage_sync.py`.

| What | Where it lives now |
|---|---|
| Employees, attendance, payroll … | Neon Postgres tables (as before, just Postgres instead of SQLite) |
| Face photos (already AES-encrypted), `face_data.pkl`, `embeddings_cache.pkl`, payslips, `license.lic` | Neon table `stored_files` (restored to disk at every start) |
| Backups | Neon itself + a daily encrypted `pg_dump` kept 14 days by GitHub Actions |

"AI training" in AIAPS = computing face embeddings from the stored photos with a
pre-trained DeepFace model. The photos are the training data, so keeping the photos keeps
everything; the embeddings cache is restored too, so a restart does not re-encode all photos.

## 1. Neon (database) — free, no card
1. Sign up at neon.tech → create a project → copy the **connection string**
   (`postgresql://user:pass@ep-xxxx.neon.tech/neondb?sslmode=require`).
2. Free storage is about 1 GB per project. Photos are compressed JPEGs; roughly
   1–2 MB per employee, so a few hundred employees fit.

## 2. Render (web service)
Environment variables (Render → your service → Environment):

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string |
| `STORAGE_BACKEND` | `db` |
| `SECRET_KEY` | any long random string (**must be fixed**, otherwise it changes on every restart) |
| `FACE_DATA_ENCRYPTION_KEY` | a Fernet key, create once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **never change or lose it**, photos cannot be decrypted without it |
| `FLASK_ENV` | `production` |
| `CRON_TOKEN` | a long random string (enables `/cron/<token>`) |
| `STORAGE_LIMIT_MB` | optional, default `700` |
| `MAIL_*`, `COMPANY_NAME` … | as in `.env.example` |

**Start command (one process only):**
```
waitress-serve --host=0.0.0.0 --port=$PORT --threads=4 app:app
```
Keep the single process: it is what the face-recognition libraries need RAM-wise, and
`storage_sync` assumes one process. If the service crashes with "out of memory" while using
face recognition, the free 512 MB is too small for TensorFlow/DeepFace — move the same
setup to another free host with more RAM (nothing in the code has to change).

## 3. Keep it awake + daily flush (cron-job.org, free)
Create two jobs at cron-job.org:
* every 10 minutes: `GET https://YOUR-APP.onrender.com/cron/<CRON_TOKEN>`
* daily 02:00: `GET https://YOUR-APP.onrender.com/cron/<CRON_TOKEN>?task=backup`

(Free Render gives 750 instance-hours/month, enough for one always-on service.)

## 4. Off-site backup (GitHub Actions, free)
Push the project to a **private** GitHub repo and add the repository secrets listed at the
top of `.github/workflows/db-backup.yml` (`DATABASE_URL`, `BACKUP_PASSPHRASE`, optional
`APP_URL`, `CRON_TOKEN`). It runs daily and can be started manually (Actions → *Run workflow*).
Keep `BACKUP_PASSPHRASE` somewhere safe. Restore:
```
gpg -d aiaps_YYYY-MM-DD.sql.gz.gpg | gunzip | psql "$NEW_DATABASE_URL"
```

## 5. Check that it works
* Log in as admin → open `/storage-status`: `enabled: true`, `restored: true`, `used_mb`, `percent`.
* Register an employee with photos, then Render → *Manual Deploy → Clear cache & deploy*.
  The employee and the photos must still be there and recognition must still work.
* Storage ≥ 80 % full shows a warning in `last_error`; at the limit new files are **not**
  saved (nothing is deleted) — free space or raise `STORAGE_LIMIT_MB` (Neon plan permitting).

## Good to know
* The first request after a long sleep is slow (the app boots, restores files, loads models).
* `.env` is never stored in the database; secrets belong in Render's Environment tab.
* The Settings → Backup "Run backup now" button now means "save all files to the database now".
* Desktop installs are unaffected: without `STORAGE_BACKEND=db` nothing changes.
