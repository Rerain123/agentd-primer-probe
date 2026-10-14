#!/bin/bash
set -euo pipefail
# Run only inside the matching Job. No application catalog is changed here.
root=/data/hosts-20261012-r01
mkdir -p "$root"
exec > >(tee -a "$root/build.log") 2>&1
trap 'rc=$?; echo "HOST_BUILD_EXIT=$rc $(date -u +%FT%TZ)"' EXIT
echo "START $(date -u +%FT%TZ)"
cp /scripts/host-genomes-20261012.tsv "$root/source-manifest.tsv"
makeblastdb -version
df -h /data
export BLAST_USAGE_REPORT=false
# Reuse the validated nt taxonomy files, without writing to the nt PVC.
mkdir -p "$root/taxonomy"
for name in taxdb.bti taxdb.btd taxonomy4blast.sqlite3; do
  test -s "/nt/$name"
  cp "/nt/$name" "$root/taxonomy/$name"
done
sha256sum "$root"/taxonomy/* > "$root/taxonomy.sha256"
export BLASTDB="$root/taxonomy"
curl_fetch() {
  curl -fLsS --retry 5 --retry-delay 10 --connect-timeout 30 \
    --max-time 10800 --speed-time 300 --speed-limit 1024 "$@"
}
while IFS=$'\t' read -r species taxid accession prefix md5 bytes bases relative; do
  dir="$root/$species/$accession"
  base="https://ftp.ncbi.nlm.nih.gov/genomes/all/$relative"
  archive="${prefix}_genomic.fna.gz"
  mkdir -p "$dir"
  cd "$dir"
  # A completed version is immutable; failed builds remain available for diagnosis.
  if [ -f VALIDATION_COMPLETE ]; then
    echo "SKIP_VALIDATED $species $accession"
    continue
  fi
  if [ -e db.nsq ] || [ -e db.ndb ]; then
    echo "EXISTING_INDEX_WITHOUT_VALIDATION $dir; inspect before rebuilding"
    exit 1
  fi
  echo "DOWNLOAD $species $accession $(date -u +%FT%TZ)"
  curl_fetch "$base/${prefix}_assembly_report.txt" -o assembly_report.txt
  curl_fetch "$base/${prefix}_assembly_stats.txt" -o assembly_stats.txt
  curl_fetch "$base/md5checksums.txt" -o upstream-md5checksums.txt
  grep -Eq "^# RefSeq assembly accession: +${accession//./\.}([[:space:]]|$)" assembly_report.txt
  grep -Eq "^# Taxid: +$taxid([[:space:]]|$)" assembly_report.txt
  upstream=$(awk -v name="./$archive" '$2==name {print $1}' upstream-md5checksums.txt)
  test "$upstream" = "$md5" || { echo "UPSTREAM_CHECKSUM_CHANGED $species"; exit 1; }
  printf '%s  %s\n' "$md5" "$archive" > genomic.md5
  if ! md5sum -c genomic.md5; then
    curl_fetch "$base/$archive" -o "$archive.part"
    test "$(stat -c %s "$archive.part")" -eq "$bytes"
    printf '%s  %s\n' "$md5" "$archive.part" | md5sum -c -
    mv "$archive.part" "$archive"
  fi
  md5sum -c genomic.md5
  gzip -t "$archive"
  gzip -dc "$archive" > genome.fna.part
  mv genome.fna.part genome.fna
  echo "MAKEBLASTDB $species $(date -u +%FT%TZ)"
  makeblastdb -in genome.fna -dbtype nucl -parse_seqids -taxid "$taxid" \
    -blastdb_version 5 -title "$species $accession" -out db > makeblastdb.log 2>&1
  blastdbcmd -db "$dir/db" -dbtype nucl -info > blastdb-info.txt
  # Verify the indexed sequence lengths match NCBI's assembly statistics.
  blastdbcmd -db "$dir/db" -entry all -outfmt '%l' > sequence-lengths.txt
  actual=$(awk '{sum+=$1} END {printf "%.0f",sum}' sequence-lengths.txt)
  echo "BASES expected=$bases actual=$actual"
  test "$actual" -eq "$bases"
  blastdbcheck -db "$dir/db" -dbtype nucl -verbosity 3 > blastdbcheck.log 2>&1
  cat blastdbcheck.log
  sha256sum genome.fna db.* > database-files.sha256
  date -u '+VALIDATION_COMPLETE %Y-%m-%dT%H:%M:%SZ' > VALIDATION_COMPLETE
  cat VALIDATION_COMPLETE
done < /scripts/host-genomes-20261012.tsv
date -u '+HOST_DATABASES_COMPLETE %Y-%m-%dT%H:%M:%SZ' > "$root/HOST_DATABASES_COMPLETE"
du -sh "$root"
cat "$root/HOST_DATABASES_COMPLETE"
