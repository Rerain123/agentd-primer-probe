# 服务器数据目录约定

生产数据库位于安装包之外，通过只读卷挂载到容器。

推荐宿主机布局：

```text
/srv/agentd-data/
├── blastdb/
│   ├── cross_species/
│   │   └── 2026-07-01/db.{nhr,nin,nsq,...}
│   ├── host/
│   │   ├── sus_scrofa/2026-07-01/db.{nhr,nin,nsq,...}
│   │   ├── bos_taurus/2026-07-01/db.{nhr,nin,nsq,...}
│   │   └── gallus_gallus/2026-07-01/db.{nhr,nin,nsq,...}
│   └── target/
│       └── CSFV/2026-07-01/db.{nhr,nin,nsq,...}
└── sequences/
    ├── target/
    │   └── CSFV/2026-07-01.fasta
    └── internal/
        └── pig/
            ├── ACTB/2026-07-01.fasta
            ├── GAPDH/2026-07-01.fasta
            └── 18S_rRNA/2026-07-01.fasta
```

`deploy/.env` 映射：

```dotenv
AGENTD_BLASTDB_DIR=/srv/agentd-data/blastdb
AGENTD_SEQUENCE_DATA_DIR=/srv/agentd-data/sequences
```

容器内路径始终使用 `/data/blastdb` 和 `/data/sequences`。例如：

```json
{
  "resources": {
    "target:CSFV:sequences": {
      "kind": "fasta",
      "path": "/data/sequences/target/CSFV/2026-07-01.fasta",
      "version": "2026-07-01",
      "required": false
    },
    "target:CSFV:blast": {
      "kind": "blast_database",
      "path": "/data/blastdb/target/CSFV/2026-07-01/db",
      "version": "2026-07-01",
      "required": false
    }
  }
}
```

版本字段不得使用 `SET_ON_SERVER`。BLAST 资源填写 `blastn -db` 所用前缀；Provider 可识别普通和分卷索引。目录升级采用“新增版本目录 → 校验 → 修改目录配置 → 重启”的方式，保留旧目录用于回滚。
