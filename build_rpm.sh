#!/usr/bin/env bash
set -euo pipefail

# Sestaví lokální RPM z aktuálního source tree.
# Vypíše absolutní cestu k výslednému RPM na stdout.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC_FILE="${SCRIPT_DIR}/game-mover.spec"
RPM_NAME="game-mover"

version="$(awk -F': *' '$1 == "Version" { print $2; exit }' "${SPEC_FILE}")"
if [[ -z "${version}" ]]; then
  echo "Nelze načíst verzi ze spec souboru." >&2
  exit 1
fi

topdir="${TMPDIR:-/tmp}/game-mover-rpmbuild-${UID}"
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

mkdir -p "${topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

pkgroot="${tmpdir}/${RPM_NAME}-${version}"
mkdir -p "${pkgroot}"

rsync -a --no-owner --no-group \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.vscode' \
  --exclude '.ruff_cache' \
  --exclude '.pytest_cache' \
  --exclude '.mypy_cache' \
  --exclude '.coverage' \
  --exclude 'htmlcov' \
  --exclude '__pycache__' \
  --exclude '.rpmbuild' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '*.key' \
  --exclude '*.log' \
  --exclude '*.pyc' \
  --exclude '*.bck' \
  "${SCRIPT_DIR}/" "${pkgroot}/"

tar -C "${tmpdir}" -czf "${topdir}/SOURCES/${RPM_NAME}-${version}.tar.gz" "${RPM_NAME}-${version}"
cp "${SPEC_FILE}" "${topdir}/SPECS/"

TMPDIR="${TMPDIR:-/tmp}" \
rpmbuild \
  --define "_topdir ${topdir}" \
  --define "_tmppath ${TMPDIR:-/tmp}" \
  -ba "${topdir}/SPECS/$(basename "${SPEC_FILE}")" >/dev/null

rpm_path="$(ls -1t "${topdir}/RPMS/noarch/${RPM_NAME}-${version}"-*.noarch.rpm | head -n 1)"
if [[ -z "${rpm_path}" ]]; then
  echo "RPM se nepodařilo vytvořit." >&2
  exit 1
fi

printf '%s\n' "${rpm_path}"
