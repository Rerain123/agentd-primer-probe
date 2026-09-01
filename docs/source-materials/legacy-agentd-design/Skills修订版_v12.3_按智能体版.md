# 分阶段Skills开发清单 v12.3（按智能体版）

> **v12核验日期**：2026-06-18 | **基于v11，分析报告+用户讨论修订**
> **v12修订要点**：删除BindCraft/AbNatiV/Sapiens；降级AF-Multimer P0b→P2、FrameFlow P1→P2；新增C0/A3c/V2/P3-P5；补回C31-C41(FoldX)。P0b 11→9。
> **v12.1修订日期**：2026-06-19 | C35拆分+C42+B7+V3+V4新增；A3b降P1→P2；A3c Experimental。71→77。
> **v12.2修订日期**：2026-06-19 | **P0优先级重整+依赖倒挂修复**：C8 P0b→P0c；C13b/C14/C21/B1c P0b→P1；A4 P0c→P1；C34 P2→P1；B7去C14依赖。C8b/C9/C12/B7保留P0b。依赖链无倒挂。
> **v12.3修订日期**：2026-06-22 | **v12.3-final (2026-06-22)**：C6b MMseqs2升级 P1→P0a（与C5 MAFFT/C6 BLAST同级基础IO）。C41合并入C9；新增C6b MMseqs2(大规模序列预筛选, GPLv3, P0a)；新增A8 Vaxign-ML(反向疫苗学保护性抗原预测, Docker开源)；C42追加SKEMPI 2.0数据库引用。NUPACK否决。77→78。详见 `Skills v12.3修改方案_基于两份外部评审.md`。
> **列说明**：P0a=M1-M2 | P0b=M3-M4 | P0c=M5-M6 | P1=M7-M12 | P2=M13-M24

---

## 通用公共底座

### 阶段一：基础 IO

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C0 | 畜禽病原体数据库检索 `[v12新增]` | 从NCBI Pathogen Detection/GISAID/GenBank检索畜禽病原基因组序列+元数据(宿主/采集地/时间/血清型/基因型)。BioPython Entrez封装NCBI,GISAID需注册账号。为抗原设计和引物设计提供真实流行株序列输入 | P0a | fetch_pathogen_sequences | NCBI Pathogen: https://www.ncbi.nlm.nih.gov/pathogens/ ；GISAID: https://gisaid.org/ (⚠️需注册)；BioPython Entrez封装。自研wrapper | 无 | 抗原/引物 | — |
| C1 | BioPython | 序列读写(FASTA/GenBank)、结构解析(PDB/mmCIF)、ProtParam物化性质。含NCBI Entrez核酸检索 | P0a | biopython_io | https://github.com/biopython/biopython (BSD, conda/pip) | `biopython` (scientific-agent-skills) | 全部 | — |
| C2 | UniProt检索 | 获取序列与功能注释 | P0a | fetch_uniprot | REST API: https://rest.uniprot.org/ （⚠️裸curl返回403正常，实际调用需加`Accept: application/json`请求头）；SDK: https://github.com/multimeric/Unipressed | `uniprot` (protein-design-skills) | 全部 | — |
| C3 | RCSB PDB检索 | 获取实验结构 | P0a | fetch_pdb | API: https://data.rcsb.org/ ；SDK: https://github.com/rcsb/py-rcsb-api | `pdb` (protein-design-skills); `structure-io` (bioskills) | 全部 | — |
| C4 | AlphaFold DB检索 | 获取预测结构 | P0a | fetch_alphafold | API: https://alphafold.ebi.ac.uk/ | `alphafold-database` (openclaw-medical-skills) | 全部 | — |
| C5 | MAFFT | 多序列比对 | P0a | run_mafft | https://mafft.cbrc.jp/alignment/software/ (BSD-like, conda) | `multiple-alignment` (bioskills) | 抗原/引物 | C1 |
| C6 | BLAST | 同源性/特异性筛查。引物场景加宿主基因组库参数(`-db sus_scrofa/bos_taurus/gallus`)排除非特异性命中 | P0a | run_blast | NCBI BLAST+ v2.16.0: https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/ (Public Domain) | `blast-searches` (bioskills, 远程BLAST); `local-blast` (bioskills, 本地BLAST+ CLI) | 全部 | C1 |
| C6b | MMseqs2 `[v12.3新增, v12.3-final升级P1→P0a]` | 超快速序列搜索+聚类——Steinegger lab(Seoul National Univ),与FoldSeek(C14)同一作者。GPLv3开源。Nature Biotech 2017+Nature Methods 2025(GPU加速版)。速度比BLAST快10-100×,ColabFold核心组件。应用场景：C0检索后大批量序列去冗余+C5(MAFFT)比对前预聚类+保守区筛选前快速同源搜索。⚠️与BLAST(C6)互补而非替代：BLAST做引物/探针小规模精确验证,MMseqs2做全基因组级别批量搜索。GPU版需NVIDIA CUDA,CPU版conda可用。🟢畜牧业适用性:物种无关的序列搜索基础设施 | P0a | run_mmseqs2 | https://github.com/soedinglab/MMseqs2 (GPLv3, conda) | 无——现有skills均未覆盖 | 全部 | C1 |
| C7 | 输出标准化 | JSON Schema统一输出 | P0a | normalize_output | Pydantic: https://github.com/pydantic/pydantic (MIT, ⭐28k) | — | 全部 | — |

### 阶段一：核心计算引擎

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C8 | **Gromacs** | MD模拟主力引擎 | P0c | run_md | 仓库: https://gitlab.com/gromacs/gromacs ；下载: https://ftp.gromacs.org/gromacs/ (LGPL-2.1+, conda) | `scholaraio-gromacs` (ZimoLiao/scholaraio) ✅已安装 | 抗原/抗体/疫苗 | C1, C3 |
| C8b | **AMBER** | MD交叉验证引擎——GPU加速(pmemd.cuda)。AmberTools CPU版(C9)免费 | P0b | run_md_amber | https://ambermd.org/ ；AmberTools: https://ambermd.org/AmberTools.php (CPU免费) | `ScienceClaw-molecular-dynamics` (Zaoqu-Liu/ScienceClaw) ✅已安装 | 抗原/抗体/疫苗 | C1, C3 |
| C9 | **AmberTools** `[v12.3合并C41]` | MD辅助+结合自由能——antechamber配体参数化/tleap拓扑构建/cpptraj轨迹分析/MMPBSA结合自由能/MMGBSA残留物能量分解。⚠️v12.3将原C41(AmberTools精修)合并入此,取消C41独立编号 | P0b | run_amber_tools | https://ambermd.org/AmberTools.php (conda, CPU免费) | `ScienceClaw-molecular-dynamics` (Zaoqu-Liu/ScienceClaw) ✅已安装 | 抗原/抗体/疫苗 | C3 |
| C10 | **ESM2** | 蛋白序列embedding + PLL评分 + 零样本突变效应预测。facebookresearch/esm已归档(archived=True, ⭐4116)；Biohub/esm为新版活跃仓库(⭐2727) | P0b | extract_esm2 | https://github.com/Biohub/esm (MIT, ⭐2727, 与C11同仓库) | `esm` (protein-design-skills + scientific-agent-skills) | 抗原/抗体/疫苗 | C1 |
| C11 | **ESMFold2** | 快速结构预测+设计序列验证——无需MSA,秒级折叠。两大独特能力:①设计序列(Non-natural)结构验证——MSA方法不适用;②大批量快速筛选(>10个蛋白)。⚠️默认结构预测走C12(AF3最高精度),C11在以下场景使用:设计序列验证/大批量筛选/C12不可用时备选。本地需GPU。抗体-抗原DockQ≈50%(bioRxiv自报,独立benchmark有限发布仅3周) | P0b | predict_structure | GitHub: https://github.com/Biohub/esm (MIT, ⭐2727, 2026.05.27)；pip: `pip install esm@git+https://github.com/Biohub/esm.git@main`；HF: https://huggingface.co/Synthyra/ESMFold2 | `esm` (scientific-agent-skills) 提及ESMFold2; 🌐`esm-protein-language-model` (SciAgent-Skills) 含结构预测 | 抗原/抗体/疫苗 | C3, C4 |
| C12 | **AlphaFold3** `[v12.3定位修正:备选→主力]` | 主力结构预测——代码CC BY-NC-SA(⚠️不可商用),权重需Google申请。Ab-Ag精度~46%(2025独立benchmark)最高之一。**默认引擎**。非商用场景首选;需商用或大批量筛选时走C11(ESMFold2)或C13b(Boltz-2) | P0b | predict_structure_af3 | https://github.com/google-deepmind/alphafold3 (CC BY-NC-SA) | `modern-structure-prediction` (bioskills) 含AF3 server API完整覆盖 | 抗原/抗体/疫苗 | C3, C4 |
| **C13b** | **Boltz-2** `[v11新增, v12.2降级P0b→P1]` | 结构+亲和力联合预测——MIT,⭐~4k(含Boltz-1累积),`pip install boltz`。**唯一同时输出结合亲和力(ΔG)的AI模型**,近FEP精度快1000×+。⚠️Ab-Ag DockQ~21-28%弱于AF3(~46%),趋势向好。⚠️与AF3/HADDOCK3互补而非替代：Boltz-2做结构+亲和力联合预测,AF3做最高精度结构预测,HADDOCK3做信息驱动蛋白对接——三者解决不同问题。Boltz-2亲和力输出可参考替代PRODIGY(C34)的界面亲和力估算,但不替代HADDOCK3对接功能。⚠️v12.2降级:最小闭环中可用C12(默认)+C34(PRODIGY,亲和力估算)替代结构+亲和力联合预测,但Boltz-2结构+亲和力联合输出为独特能力(唯一同时输出ΔG的AI模型) | P1 | predict_structure_affinity | https://github.com/jwohlwend/boltz (MIT, pip install, curl 200 OK) | `boltz` (protein-design-skills) ✅已安装 | 抗原/抗体/疫苗 | C3, C4 |
| C14 | **FoldSeek** `[v12.2降级P0b→P1]` | 结构相似性搜索——Nature Biotechnology 2024(Steinegger lab), Foldseek-Multimer Nature Methods 2025。已集成ChimeraX。⚠️仅抗原流程一处主动调用,抗体中为B7(SAbDab)的补充,去掉不影响最小闭环 | P1 | search_structure | https://github.com/steineggerlab/foldseek (GPLv3, conda) | `foldseek` (protein-design-skills) | 抗原/抗体 | C11 |

### 阶段一：序列设计与密码子

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C15 | **ProteinMPNN** | 序列设计（通用）——Baker Lab, Science 2022, ⭐1755, MIT | P0b | design_sequence | https://github.com/dauparas/ProteinMPNN (MIT) | `proteinmpnn` (protein-design-skills) | 抗原/抗体/疫苗 | C11 |
| **C15b** | **IgDesign** `[v11新增]` | 抗体CDR逆折叠设计——ProteinMPNN抗体特化版,IgMPNN编码器+ESM2-3B解码器,8抗原SPR全验证,AbSci(上市公司)。优于vanilla ProteinMPNN在抗体CDR场景。⚠️仅抗体agent使用 | P1 | design_antibody_sequence | https://github.com/AbSciBio/IgDesign (MIT, ⭐53, curl 200 OK) | 无——`proteinmpnn`仅部分相关 | 抗体 | C15, B1 |
| C16 | **SolubleMPNN** | 溶解度优化——ProteinMPNN溶解度增强变体。⚠️仅大肠杆菌表达有效 | P1 | design_sequence_soluble | https://github.com/dauparas/ProteinMPNN (MIT, `--use_soluble_model`激活) | `solublempnn` (protein-design-skills) | 疫苗/抗原 | C15 |
| C17 | DNA Chisel | 密码子优化（多宿主：大肠杆菌/CHO/酵母），MIT | P0b | codon_optimize | https://github.com/Edinburgh-Genome-Foundry/DnaChisel (MIT, pip) | 无 | 抗原/抗体/疫苗 | C1 |
| C18 | **ESM-IF1** | Inverse folding正交验证（通用） | P1 | validate_design_esmif | https://github.com/Biohub/esm (MIT, 与C10/C11同仓库) | `esm` (scientific-agent-skills) 提供ESM3逆折叠; 🌐`esm-protein-language-model` (SciAgent-Skills) | 抗原/抗体/疫苗 | C11, C15 |
| **C18b** | **AntiFold** `[v11新增]` | 抗体逆折叠验证——ESM-IF1抗体微调版,Oxford OPIG,BSD-3,SAbDab微调。与C15b IgDesign正交验证 | P1 | validate_antibody_design | https://github.com/oxpig/AntiFold (BSD-3, ⭐165, curl 200 OK) | 无——`esm` skill仅覆盖ESM2 | 抗体 | C18, B1 |

### 阶段一：通用性质评估

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C19 | **Protein QC** | 结构QC(pLDDT/pTM/scRMSD/PAE)+表达QC(Instability/GRAVY/pI)+结合QC(ipTM/SC/dG)+liability扫描(NG/NT/NS deamidation等)+复合评分 | P0c | qc_designs | BioPython ProtParam + ESM2 PLL + PyRosetta: http://www.pyrosetta.org/ (学术免费) | `protein-qc` (protein-design-skills) | 全部 | C11, C10, C15 |

### 阶段一：免疫信息学共享 · 系统进化

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C20 | IEDB数据库检索 | 查询已知表位/TCR/抗体 | P0a | fetch_epitopes | REST API: https://query-api.iedb.org/ | `epitope-prediction`等bioskills免疫信息学系列(内置IEDB API) | 抗原/抗体 | — |
| C21 | IQ-TREE `[v12.2降级P0b→P1]` | 系统发育树+保守区筛选（GPLv2，引用1万+次）。⚠️保守区筛选提升引物/抗原设计特异性,但非最小闭环阻塞项——可对全长序列设计后由BLAST(C6)过滤 | P1 | run_phylogenetics | http://www.iqtree.org/ (GPLv2) | `phylogenetics` (scientific-agent-skills + bioskills) | 抗原/引物 | C5 |

### 阶段二：共享分析工具

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C22 | **HADDOCK3** | 蛋白-蛋白对接——Utrecht Bonvin Lab, 20年+, latest=2026.5.0。⚠️CNS引擎需Biovia授权,学术可切换OpenMM引擎 | P1 | dock_protein | https://github.com/haddocking/haddock3 (Apache 2.0 Python层) | 无——`diffdock`是小分子对接 | 抗原/抗体 | C11 |
| C23 | **MD分析管线** | RMSD/RMSF/Rg/PCA/界面稳定性（统一管线） | P1 | analyze_md_results | MDTraj: https://github.com/mdtraj/mdtraj (LGPL-2.1)；MDAnalysis: https://github.com/MDAnalysis/mdanalysis (GPL-2.0) | `molecular-dynamics` (scientific-agent-skills); `mdtraj-trajectory-analysis` + `mdanalysis-trajectory` (sciagent-skills) | 抗原/抗体/疫苗 | C8 |
| C24 | pH稳定性分析 | pH依赖稳定性(pH4-9多条件扫描) | P1 | assess_ph_stability | PROPKA: https://github.com/jensengroup/propka (MIT, pip) | 无——`virtual-screening`仅用PROPKA固定pH质子化,不做多条件pH扫描 | 抗体/疫苗 | C11 |
| C25 | 糖基化位点预测 | N-/O-糖基化预测（⚠️O-糖基化精度存疑） | P1 | predict_glycosylation | Web: https://services.healthtech.dtu.dk/services/NetNGlyc-1.0/ (DTU学术免费) | `glycoengineering` (scientific-agent-skills) | 抗体/疫苗 | C1 |
| C26 | 信号肽切割预测 | 分泌信号识别 | P1 | predict_signal_peptide | USPNet: https://github.com/ml4bio/USPNet (MIT, ⭐35, Nature Compt Sci 2023)；备选SignalP 6.0 Web | — | 抗体/疫苗 | C1 |
| C27 | 滴度数据分析 | ELISA/Octet结构化解析 | P1 | analyze_titer | 自研（openpyxl+pandas+scipy） | — | 抗体/疫苗 | — |
| C28 | 中和效价分析 | IC50/IC90四参数logistic拟合 | P1 | analyze_neutralization | 自研（scipy curve_fit） | — | 抗体/疫苗 | — |

### 阶段二：共享输出

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C29 | 候选对比报告 | 多候选横向比较（参数区分生物类型） | P1 | compare_candidates | 自研（matplotlib+Jinja2+scipy） | — | 全部 | C27, C28 |
| C30 | 结果解释模块 | 工具JSON→科研自然语言 | P1 | explain_results | LLM(示例使用OpenAI API,可替换为任意provider) | — | 全部 | C7 |

### 阶段二/三：工具补充

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C13 | AlphaFold-Multimer `[v12降级P0b→P2]` | 蛋白复合物预测——Apache 2.0可商用。⚠️Ab-Ag成功率~13%(2025独立benchmark),已被AF3(46%)/Boltz-2(28%)/ESMFold2(50%)全面超越。保留作为兼容备选 | P2 | predict_complex | https://github.com/google-deepmind/alphafold (`--model_preset=multimer`, Apache 2.0) | `alphafold` (protein-design-skills) | 抗原/抗体 | C11 |

### 阶段三：实验闭环（四个智能体共享）

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| C31 | 实验结果结构化解析 | 解析ELISA/表达/结合/中和等实验数据 | P2 | parse_experiment_results | openpyxl+pandas | — | 全部 | — |
| C32 | 预测-实验偏差分析 | 偏差归因与统计检验 | P2 | analyze_prediction_gap | scipy+statsmodels | — | 全部 | C31 |
| C33 | 多轮版本效果对比 | 迭代效果量化比较 | P2 | compare_iterations | sklearn | — | 全部 | C32 |
| C34 | 关键相互作用分析 `[v12.2升级P2→P1]` | 结合界面残基+氢键+疏水+SASA | P1 | analyze_interactions | FreeSASA: https://github.com/mittinatten/freesasa (MIT, ⭐194)；PRODIGY: https://github.com/haddocking/prodigy (Apache 2.0, ⭐182, v2.4.0, `pip install prodigy-prot`) | `geometric-analysis` (bioskills) 含SASA; `mdtraj`/`mdanalysis` (sciagent) 含SASA | 抗原/抗体/疫苗 | C8 |
| **C35** | **FoldX 稳定性分析** `[v12补充, v12.1从P2提至P1]` | 突变ΔΔG计算——FoldX v5.1(http://foldxsuite.crg.eu/ , 学术免费⚠️需注册邮件获取下载链接不可直接wget, 2025-02 Bioinformatics更新)。支持单体蛋白稳定性+蛋白-蛋白界面ΔΔG(复合物模式)。⚠️仅筛ΔΔG>+5 kcal/mol极端不稳定突变；经验力场精度~1-2 kcal/mol,不替代物理力场精筛 | P1 | analyze_stability_foldx | FoldX v5.1: http://foldxsuite.crg.eu/ (学术免费,需注册) | 无——`protein-qc`含PyRosetta界面指标但不含FoldX | 抗原/抗体/疫苗 | C11(结构) |
| C36 | 结果追溯导出 | 审计与终验支撑 | P2 | export_traceability | 自研（JSON/YAML+时间戳+版本号） | — | 全部 | — |
| C37 | 实验报告解读 | LLM解读实验PDF/Word | P2 | summarize_experiment | LLM (同C30) | — | 全部 | C31 |
| C38 | 终验报告生成 | 自动生成终验材料 | P2 | generate_acceptance_report | Jinja2+matplotlib | — | 全部 | C36 |
| C39 | 失败案例归档 | 沉淀失败案例+归因 | P2 | archive_failed_cases | SQLite/PostgreSQL | — | 全部 | C32 |
| C40 | 科学规则版本管理 | 规则+cutoff+参数版本追踪 | P2 | manage_rules_version | Git+DVC | — | 全部 | — |
| **C42** | **系统性突变扫描** `[v12.1新增]` | 位点饱和突变ΔΔG扫描——对指定残基逐一突变为20种标准氨基酸,计算每个突变的稳定性变化(ΔΔG)。三引擎分层：①**FoldX PositionScan**(C35,P1):快速经验力场,~1min/位点,支持单体+复合物界面ΔΔG,适合初筛；②**ThermoMPNN**(C43,P1):深度学习预测,~1min/蛋白,⚠️仅单体蛋白(不适用抗体CDR界面/多链复合物)；③**Rosetta flex_ddg**(C44,P2):物理力场+回弯采样,~30min/位点,MPI多CPU并行,适合精筛(⚠️需UW许可,获取难,标注可选)。应用场景：抗原单体热点优化(稳定化/去稳定化突变排序)、抗体亲和力成熟(CDR位点扫描,FoldX主力,ThermoMPNN不可用于界面)、疫苗稳定性工程(融合蛋白,FoldX+可选Rosetta)。输出：20×N突变矩阵+ΔΔG热图+三引擎一致性标注(仅单体时ThermoMPNN参与交叉验证)。⚠️FoldX PositionScan对大蛋白(>500残基)需限定扫描区域；三引擎ΔΔG绝对值不可直接对比(FoldX经验力场 vs ThermoMPNN深度学习 vs Rosetta物理力场),仅用于各自体系内排序；DDGScan(https://github.com/JinyuanSun/DDGScan, MIT)可作并行封装。⚠️SKEMPI 2.0数据库(7,085个突变ΔΔG基准,Bioinformatics 2019)可作为FoldX/ThermoMPNN预测精度的外部验证参考——非工具不增编号,作为C42内部引用数据源。🟢畜牧业适用性:作为ΔΔG预测器的benchmark标准与靶标物种无关——验证的是计算方法精度而非生物学场景 | P1 | scan_mutations | FoldX PositionScan: http://foldxsuite.crg.eu/command/PositionScan (复用C35安装)；ThermoMPNN: https://github.com/Kuhlman-Lab/ThermoMPNN (C43)；Rosetta flex_ddg: https://github.com/RosettaCommons/rosetta (复用C44安装)；DDGScan(并行封装,可选): https://github.com/JinyuanSun/DDGScan (MIT) | 无——protein-qc仅含PyRosetta界面指标,不含FoldX PositionScan/ThermoMPNN/Rosetta | 抗原/抗体/疫苗 | C11(结构), C35(FoldX), C43(ThermoMPNN,仅单体), C44(Rosetta,可选P2) |
| **C43** | **ThermoMPNN 单体稳定性预测** `[v12.1新增]` | 深度学习突变ΔΔG预测——Kuhlman-Lab(UNC), PNAS 2024。基于ProteinMPNN迁移学习,预测单点突变对蛋白稳定性的影响(ΔΔG)。⚠️**仅单体蛋白**：训练数据均为单体蛋白,不支持蛋白-蛋白界面/复合物/多链稳定性预测；Neuraminidase四聚体实验验证失败(11个预测突变无一改善结合)；82.5%预测偏向溶剂暴露残基；序列同源性<25%时PCC降至~0.34。**适用场景**：抗原单体热稳定性优化；**不适用**：抗体CDR亲和力成熟(界面残基)、多价融合蛋白稳定性 | P1 | predict_stability_thermompnn | ThermoMPNN: https://github.com/Kuhlman-Lab/ThermoMPNN (学术可用)；ThermoMPNN-D(双突变): https://github.com/Kuhlman-Lab/ThermoMPNN-D | 无——现有skills均未覆盖 | 抗原 | C11(结构) |
| **C44** | **Rosetta 精修** `[v12.1从C35拆出,保留P2]` | 物理力场突变ΔΔG精筛——Rosetta cartesian_ddg/flex_ddg(https://github.com/RosettaCommons/rosetta , 学术免费⚠️需UW学术许可,获取周期长)。支持单体+复合物界面ΔΔG,精度高于FoldX~1 kcal/mol但计算成本高(~30min/位点,MPI多CPU并行)。⚠️保留在P2:获取难度高(需审批),如无法获取则C42仅用FoldX+ThermoMPNN双引擎 | P2 | analyze_stability_rosetta | Rosetta: https://github.com/RosettaCommons/rosetta (学术免费,需UW许可) | 无——PyRosetta(python wrapper)≠Rosetta flex_ddg(需UW许可) | 抗原/抗体/疫苗 | C11(结构) |

---

## 智能体一：抗原设计与优化智能体

> 回答"选哪个抗原、哪段序列、如何优化抗原本身"。
> 共享底座引用：C0(病原数据库)、IO(C1-C7,含C6b)、核心引擎(C8-C14,含C13b Boltz-2)、序列设计(C15/C18)、密码子(C17)、QC(C19)、IEDB(C20)、进化(C21)、对接(C22)、MD(C23)、pH(C24)、输出(C29-C30)、实验闭环(C31-C44, C41已合并入C9)

### 独有工具

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|
| A1 | **NetMHCpan 4.2** + MHCflurry | MHC-I肽段结合预测。NetMHCpan-4.2训练数据含猪SLA/牛BoLA/马Eqca实测BA+EL数据(171种MHC分子),PPV=0.8291 vs MHCflurry 0.7256(P<10⁻⁹)。MHCflurry Apache 2.0作为补充 | P0b | predict_mhci_binding | NetMHCpan 4.2: https://services.healthtech.dtu.dk/services/NetMHCpan-4.2/ ；IEDB本地: https://downloads.iedb.org/tools/mhci/LATEST/ ；MHCflurry: https://github.com/openvax/mhcflurry (Apache 2.0, conda) | `bio-immunoinformatics-mhc-binding-prediction` (bioskills) | — |
| A2 | **NetMHCIIpan 4.3** | MHC-II肽段结合预测。65万+BA+EL,新增牛BoLA-DRB3 EL数据。DTU Nielsen Lab,学术免费 | P0b | predict_mhcii_binding | DTU 4.3: https://services.healthtech.dtu.dk/services/NetMHCIIpan-4.3/ ；IEDB本地: https://downloads.iedb.org/tools/mhcii/LATEST/ | `mhc-class-ii-prediction` (bioskills) | — |
| A3 | **BepiPred 3.0** | 线性B细胞表位预测——领域标准工具,Protein Science 2022,167引用,基于ESM-2。DTU Web高频使用 | P0b | predict_bcell_epitope_linear | https://github.com/UberClifford/BepiPred-3.0 (DTU学术, ⭐24)；Web: https://services.healthtech.dtu.dk/services/BepiPred-3.0/ | `bio-immunoinformatics-epitope-prediction` (bioskills) | C10 |
| **A3b** | **BigMHC** `[v11新增, v12.1降级P1→P2]` | MHC-I表位呈递+免疫原性预测——Nature MI 2023, Johns Hopkins。IEDB API不做免疫原性。⚠️**v12.1降级理由**：AUROC 0.9733基于人类HLA训练数据(**无SLA/BoLA/BF实测数据**),对畜禽MHC泛化效果无验证。畜禽场景以NetMHCpan(A1,有SLA/BoLA训练数据)为核心,BigMHC仅作实验性补充。⚠️JHU自定义许可(非MIT) | P2 | predict_immunogenicity_bigmhc | https://github.com/KarchinLab/bigmhc (JHU许可, Nature MI 2023, git clone无GPU) | `immunogenicity-scoring` (bioskills) 列BigMHC-IM为工具之一 | A1 |
| **A3c** | **EVEscape** `[v12新增, v12.1修正]` | 病毒免疫逃逸突变预测——Harvard Marks Lab, Nature 2023, MIT。基于进化序列+结构特征预测残基级逃逸风险评分。⚠️**Experimental/Research-only**：当前仅在SARS-CoV-2充分验证；畜禽主要病原(ASFV/PCV2为DNA病毒,PRRSV/PEDV为慢变异RNA病毒)EVEscape增益有限；真正高价值场景为禽流感(H5N1/H7N9)等快变异RNA病毒。**不进入默认workflow**,仅在用户明确要求逃逸分析时调用 | P1 | predict_immune_escape | https://github.com/OATML-Markslab/EVEscape (MIT, Python, curl OK) | 无 | C5(MAFFT多序列比对) |
| A4 | **DiscoTope-3.0** `[v12.2降级P0c→P1]` | 构象B细胞表位预测（需3D结构）——Frontiers Immunology 2024,DTU。基于ESM-IF1 inverse folding。⚠️依赖C18(ESM-IF1,P1),非最小闭环必需——A3(BepiPred,线性表位)可独立给出候选表位,A4补充构象维度 | P1 | predict_bcell_epitope_conformational | https://github.com/Magnushhoie/DiscoTope-3.0 (DTU学术, ⭐18, curl 200 OK) | `bio-immunoinformatics-epitope-prediction` (bioskills) | C11, C18 |
| A5 | **DeepTMHMM** | 跨膜螺旋预测——DTU+Biohub。⚠️GitHub无官方仓库,官方通道DTU Web或BioLib | P0b | predict_transmembrane | DTU Web: https://services.healthtech.dtu.dk/services/DeepTMHMM-1.0/ ；BioLib: `pip install biolib` | 无 | C1 |
| A6 | **IEDB Immunogenicity** | T细胞表位免疫原性评分——IEDB API,支持任意MHC等位基因。⚠️DeepImmuno已删除:仅人类HLA | P1 | score_immunogenicity | IEDB API: https://tools.iedb.org/immunogenicity/help/ | `bio-immunoinformatics-immunogenicity-scoring` (bioskills) | A1, A2 |
| A7 | **VaxiJen + AllerTOP** | 保护性抗原预测+致敏性筛查——反向疫苗学标配。VaxiJen:~1,480引用。⚠️两者Web工具(浏览器正常),无本地版。⚠️仅~3%有实验验证 | P1 | predict_antigenicity_allergenicity | VaxiJen: https://www.ddg-pharmfac.net/vaxijen/VaxiJen/VaxiJen.html ；AllerTOP: https://www.ddg-pharmfac.net/AllerTOP/ (⚠️ddg双写) | 无——`epitope-prediction`仅一行提及VaxiJen,AllerTOP未覆盖 | — |
| A8 | Vaxign-ML `[v12.3新增]` | 反向疫苗学保护性抗原预测——University of Michigan He Lab(VIOLINet),Bioinformatics 2020。监督机器学习(XGBoost+RF+SVM)预测保护性抗原。2024年集成ESM-1b embedding(1,789维特征),AUROC 0.97。Docker+Web双模式,内置"Similarity to Pig Proteins"宿主蛋白过滤。⚠️训练数据以细菌保护性抗原为主(Protegen数据库),病毒抗原占比少；未检索到ASFV/PRRSV/PCV2/PEDV/H5N1的发表验证用例；畜禽病毒场景需自行验证,不可替代实验攻毒保护性数据。与A7(VaxiJen+AllerTOP)互补而非替代——VaxiJen偏抗原性评分,Vaxign-ML偏保护性抗原筛选。🟡畜牧业适用性:结构支持(含猪蛋白过滤+病毒模式)但缺乏畜禽病毒发表验证,与A7处境相同(~3%验证率)——公平保留,诚实标注 | P1 | predict_protective_antigen | Docker: https://github.com/VIOLINet/Vaxign-ML-docker ；Web: http://www.violinet.org/vaxign/vaxign-ml ；Vaxign2: http://www.violinet.org/vaxign2 | 无 | C11(结构特征辅助) |

### 流程与依赖

```
C0(病原数据库)→C6b(MMseqs2快速聚类)→C1→A5(跨膜域)→C5(MAFFT)→C21(IQ-TREE)→保守区筛选
C11(ESMFold2)+C13b(Boltz-2)→A4(DiscoTope)+C14(FoldSeek)+C15(ProteinMPNN)
C10(ESM2)→A3(BepiPred)
A1(NetMHCpan+MHCflurry)+A2(NetMHCIIpan 4.3)→A6(IEDB Immunogenicity)→A3b(BigMHC)
C5→A3c(EVEscape, ⚠️Experimental, 仅快变异RNA病原(禽流感等)时按需调用)
A1+A2+A3+A3b→A7(VaxiJen+AllerTOP)
A1+A2+A3+A3b→A8(Vaxign-ML, P1, 保护性抗原预测, ⚠️细菌训练为主)
C11→C18(ESM-IF1)←C15(ProteinMPNN)
C11→C22(HADDOCK3)
C8+C13b→C23(MD管线)→C34(相互作用)+C35(稳定性含FoldX)
C19(Protein QC)→C17(DNA Chisel)→C29→C30
```

### 抗原智能体独有工具汇总

| 阶段 | 独有 | 引用公共底座 |
|------|------|-------------|
| P0a | — | C0-C7(含C6b MMseqs2), C20 |
| P0b | A1(NetMHCpan+MHCflurry), A2(NetMHCIIpan 4.3), A3(BepiPred), A5(DeepTMHMM) | C8-C15, C17, C21 |
| P0c | — | C19 |
| P1 | A4(DiscoTope-3.0, v12.2降级), A3c(EVEscape,Experimental), A6(IEDB Immunogenicity), A7(VaxiJen+AllerTOP), A8(Vaxign-ML, v12.3新增) | C6b, C15b, C16, C18, C18b, C22-C30 |
| P2 | A3b(BigMHC, v12.1降级) | C31-C44(含C13 AF-Multimer; C41已合并入C9) |

---

## 智能体二：抗体设计与优化智能体

> 回答"抗体序列如何优化、如何排序、如何评估可开发性"。
> 共享底座引用：C0(病原数据库)、IO(C1-C7,含C6b)、核心引擎(C8-C14,含C13b)、序列设计(C15-C18,含C15b/C18b)、密码子(C17)、QC(C19)、IEDB(C20)、对接(C22)、MD(C23)、pH(C24)、糖基化(C25)、信号肽(C26)、滴度/中和(C27-C28)、输出(C29-C30)、实验闭环(C31-C44, C41已合并入C9)

### 独有工具

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|
| B1 | IgFold | 抗体Fv快筛(<1min)——⭐420,Nature Comms 2023,JHU Gray Lab。⚠️JHU非商业学术许可 | P0b | predict_antibody_structure | https://github.com/Graylab/IgFold (JHU学术许可, `pip install igfold`) | 无——`ipsae`是binder评分非抗体结构预测 | C11 |
| **B1b** | **ImmuneBuilder** `[v11新增]` | 免疫受体结构预测——Oxford OPIG,BSD-3完全开源。4模型集成:ABodyBuilder2(抗体)+NanoBodyBuilder2(VHH纳米抗体)+TCRBuilder2(TCR)。比AF2-Multimer快100×。**唯一覆盖VHH+TCR的开源工具** | P0b | predict_immune_structure | https://github.com/oxpig/ImmuneBuilder (BSD-3, ⭐182, curl 200 OK) | 无——`ipsae`是SAE评分非结构预测 | C11 |
| **B1c** | **ABlooper** `[v11新增, v12.2降级P0b→P1]` | CDR环精修——Oxford OPIG,BSD-3,PyPI。等变DL专攻CDR构象(特别是CDR-H3),RMSD 2.49Å。⚠️B1(IgFold)+B1b(ImmuneBuilder)已输出抗体3D结构,ABlooper是精度增强非基础能力 | P1 | model_cdr_loops | https://github.com/oxpig/ABlooper (BSD-3, ⭐52, PyPI, curl 200 OK) | 无 | B1 |
| B2 | **ANARCI** | 抗体Kabat/Chothia/IMGT编号——⭐276,BSD-like,Oxford OPIG,conda | P0b | number_antibody | https://github.com/oxpig/ANARCI (BSD-like, conda) | 无 | B1 |
| **B7** | **SAbDab/OAS抗体检索** `[v12.1新增]` | 已知抗体结构/序列检索——Oxford OPIG, CC BY许可,每周自动更新。"先查已知→再考虑设计"的Retrieve路线核心工具。三个子库：①SAbDab：10,800+抗体结构(截至2025年底),按抗原/物种/CDR序列相似度检索,含亲和力数据(Kd)；②SAbDab-nano：2,200+纳米抗体结构子库；③OAS(Observed Antibody Space)：20亿+天然抗体序列,多物种免疫组库。输入：靶标抗原名称或抗体VH/VL序列→输出：相似抗体列表+结构+亲和力+CDR比对。⚠️SAbDab为结构库(仅含有PDB结构的抗体),非序列全集；畜禽来源抗体结构较少,但可作为人/小鼠同源抗体的设计起点 | P0b | search_known_antibodies | SAbDab: https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab (CC BY, REST API, 每周自动更新)；OAS: https://opig.stats.ox.ac.uk/webapps/oas/ (CC BY)；SAbDab-nano含于SAbDab | 无——现有skills均未覆盖抗体数据库检索 | B2(ANARCI编号后查询) |
| B3 | **RFDiffusion v3** | 抗体从头设计backbone——Baker Lab,Nature 2023,BSD,⭐2907。v3全原子版(RosettaCommons/foundry,⭐848) | P1 | design_antibody_de_novo | v2: https://github.com/RosettaCommons/RFdiffusion (BSD)；v3: https://github.com/RosettaCommons/foundry | `rfdiffusion` (protein-design-skills) | C11 |
| B4 | **RFantibody** | 全长抗体从头设计(VH/VHH)——Baker Lab,**Nature 2026 Jan(Vol 649,在线2025.11)**,MIT,⭐503。⚠️命中率1-2%需9000+候选+高通量筛选 | P1 | design_antibody_full | https://github.com/RosettaCommons/RFantibody (MIT) | 无——`rfdiffusion` skill仅覆盖B3 RFDiffusion | B3 |
| B5 | TAP + TNP | 抗体可开发性分析——Oxford OPIG,学术免费。TAP:IgG(VH+VL),851临床Fv,五项指标,仅Web/SAbBox VM。TNP:纳米抗体/VHH,36临床VHH,Communications Biology 2026,BSD-3,pip。⚠️不可互换:VHH无轻链+FR2亲水,用TAP会误报 | P1 | assess_developability | TAP Web: https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabpred/tap ；TNP: https://github.com/oxpig/TNP (BSD-3, pip) | 无——`protein-qc`不含TAP/TNP | C19 |
| **B6b** | **BoltzGen** `[v11新增]` | 纳米抗体de novo——MIT,12.7万nb训练,66%靶点命中率(nM级),Adaptyv Bio湿实验验证。若课题涉及VHH场景纳入 | P1 | design_nanobody | https://github.com/HannesStark/boltzgen (MIT, curl OK) | `boltzgen` (protein-design-skills) ✅已安装 | C11 |
| **B6c** | **FrameFlow** `[v12降级P1→P2]` | SE(3) flow matching骨架生成——Microsoft+MIT,TMLR 2024,比RFDiffusion快5×采样。与B3重叠,保留为备选 | P2 | generate_backbone_flow | https://github.com/microsoft/protein-frame-flow (MIT, curl OK) | 无——`rfdiffusion`是扩散模型 | C11 |

### 流程与依赖

```
C11(ESMFold2)→B1(IgFold)+B1b(ImmuneBuilder,VHH/TCR)→B2(ANARCI)→B1c(ABlooper,CDR精修)
B7(SAbDab/OAS检索)→B2(ANARCI编号)→已知抗体作为设计起点或benchmark参考
C11→B3(RFDiffusion)/B6c(FrameFlow,P2备选)→C15(ProteinMPNN)/C15b(IgDesign,抗体CDR)
C15+C15b→C16(SolubleMPNN)+C18(ESM-IF1)/C18b(AntiFold,抗体逆折叠)
C11→B4(RFantibody,全抗体)/B6b(BoltzGen,纳米抗体)
C11+C13b(Boltz-2)→C22(HADDOCK3)
C19(Protein QC)→B5(TAP+TNP)
C24+C17→C26→C25
C8→C23→C34+C35(FoldX)
C27+C28→C29→C30
```

### 抗体智能体独有工具汇总

| 阶段 | 独有 | 引用公共底座 |
|------|------|-------------|
| P0a | — | C0-C7(含C6b MMseqs2), C20 |
| P0b | B1(IgFold), B1b(ImmuneBuilder), B2(ANARCI), B7(SAbDab/OAS) | C8-C15, C17 |
| P0c | — | C19 |
| P1 | B3(RFDiffusion), B4(RFantibody), B5(TAP+TNP), B6b(BoltzGen), B1c(ABlooper, v12.2降级) | C6b, C15b, C16, C18, C18b, C22-C30 |
| P2 | B6c(FrameFlow) | C31-C44(含C13 AF-Multimer; C41已合并入C9) |

---

## 智能体三：疫苗候选与表达方案设计智能体

> 回答"怎么表达、怎么纯化、用什么载体/宿主/标签、如何递送"。
> 共享底座引用：C0(病原数据库)、IO(C1-C7,含C6b)、核心引擎(C8-C14,含C13b)、序列设计(C15-C18)、密码子(C17)、QC(C19)、对接(C22)、MD(C23)、pH(C24)、糖基化(C25)、信号肽(C26)、滴度(C27)、输出(C29-C30)、实验闭环(C31-C44, C41已合并入C9)

### 独有工具

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|
| V1 | 标签暴露评估 | His/FLAG/GST等标签SASA分析 | P1 | assess_tag_exposure | FreeSASA: https://github.com/mittinatten/freesasa (MIT, ⭐194, conda) | `geometric-analysis` (bioskills) 含SASA计算 | C11 |
| **V2** | **多价疫苗设计** `[v12新增, Workflow-based Skill]` | ⚠️非独立计算工具,而是编排C11(ESMFold2)+C34(FreeSASA)的组合workflow。多表位串联+linker优化+连接点结构验证。输入候选表位列表→Python workflow自动选择linker类型(≤3表位用GGGGS,4-8用GGGGS×2,>8用GGGGS×3)→ESMFold2(C11)预测融合蛋白结构→FreeSASA(C34)评估表位表面可及性→输出优化后多价抗原序列+linker方案+评分。⚠️局限:ESMFold2对人工融合蛋白预测精度未经充分验证；自研Python workflow(~200行) | P1 | design_multivalent_vaccine | 自研(Python workflow,依赖C11+C34。linker库参考Rosetta LinkerDesign+文献)。非独立工具,编排层workflow | 无 | C11, C34, A1, A2 |
| **V3** | **佐剂推荐** `[v12.1新增]` | 兽用疫苗佐剂选择规则引擎——根据抗原类型(亚单位/灭活/VLP)、靶标动物(猪/牛/禽)、免疫目标(Th1/Th2/黏膜)、给药途径(肌注/皮下/口服/滴鼻)推荐佐剂方案。候选佐剂库：Montanide ISA 201/206(油乳,猪牛首选,ISA 201在牛FMD实验中保护率100% vs ISA 206的67%)、铝盐(Alum,经典低成本)、CpG ODN(Th1激活)、Poly(I:C)(dsRNA模拟)、Quil-A/ISCOM(皂苷,强细胞免疫)、Carbopol(黏膜佐剂)。输出：推荐佐剂Top 3+配伍禁忌+文献支撑。⚠️规则引擎(~300行Python+JSON知识库),非ML模型；佐剂-抗原交互作用不可计算预测,规则基于文献综述+行业经验 | P1 | recommend_adjuvant | 自研(Python规则引擎+JSON佐剂知识库。文献来源:Vaccine Adjuvants Methods&Protocols, Springer; OIE/WOAH Manual of Diagnostic Tests) | 无 | C11(结构信息辅助判断), A1/A2(免疫应答类型引导) |
| **V4** | **疫苗平台选择** `[v12.1新增]` | 疫苗递送平台决策支持——根据病原体类型、靶标动物、生产规模、冷链条件、监管路径推荐平台方案。候选平台：重组亚单位蛋白(大肠杆菌/杆状病毒/CHO)、灭活全病毒、减毒活疫苗、DNA疫苗(质粒)、病毒载体(腺病毒/痘病毒/伪狂犬病毒)、VLP(病毒样颗粒)、mRNA(脂质纳米颗粒)。输出：推荐平台Top 2-3+各平台优劣对比+生产可行性评估+监管考量。⚠️决策树型Skill(~200行Python+JSON知识库),非ML模型；mRNA兽用平台处于早期商业化阶段(Sequivity™猪流感为首例),决策树基于现有监管框架和产业实践 | P1 | select_vaccine_platform | 自研(Python决策树+JSON知识库。参考:OIE/WOAH疫苗指南;各国兽药注册技术要求) | 无 | V3(佐剂选择与平台选择联动) |

> 注：疫苗智能体多数工具来自公共底座。LinearDesign(Nature 2023,百度)仅mRNA疫苗适用且GitHub为第三方包装→未纳入。PrimeGen为LLM多智能体平台依赖GPT-4o API+机器人硬件→未纳入。

### 疫苗智能体独有工具汇总

| 阶段 | 独有 | 引用公共底座 |
|------|------|-------------|
| P0a | — | C0-C7(含C6b MMseqs2) |
| P0b | — | C8-C15, C17, C21 |
| P0c | — | C19 |
| P1 | V1(标签暴露), V2(多价设计,Workflow), V3(佐剂推荐), V4(平台选择) | C6b, C15b, C16, C18, C18b, C22-C30 |
| P2 | — | C31-C44(含C13 AF-Multimer; C41已合并入C9) |

### 流程与依赖

```
C11(ESMFold2)+C13b(Boltz-2)→C15(ProteinMPNN)→C16(SolubleMPNN)
C11→V1(标签暴露)
C17(DNA Chisel)→C26(信号肽)→C25(糖基化)
C24(pH稳定性)
C19(Protein QC)
A1+A2+A3→V2(多价疫苗设计:表位串联+linker优化+C11结构验证)
C8→C23→C34+C35(FoldX)
C27→C29→C30
```

---

## 智能体四：引物与探针设计智能体

> 回答"如何设计检测用引物探针并控制特异性和结构风险"。
> 共享底座引用：C0(病原数据库)、IO(C1/C5/C6/C6b/C7不含C2/C3/C4)、进化(C21)、输出(C29-C30)、实验闭环(C31-C44, C41已合并入C9)

### 独有工具

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|
| P1 | **Primer3** | 引物探针自动设计 + 二聚体/发卡评估(`calc_hairpin`/`calc_homodimer`)+ΔG/ΔH/ΔS/Tm(`oligotm.c`,SantaLucia 1998)+熔解曲线(`calc_tm`+温度循环)。`pip install primer3-py` | P0b | design_primer_probe | https://github.com/primer3-org/primer3 (GPL-2.0+, conda)；Python: `pip install primer3-py` | `bio-qpcr-primers` + `bio-primer-validation` (bioskills) | C5 |
| P2 | 引物特异性评估 | BLAST特异性 + Primer3 thermoAlign。⚠️v12增强:显式加宿主基因组库参数`-db sus_scrofa/bos_taurus/gallus`排除非特异性命中 | P0b | evaluate_primer_specificity | Primer3 thermoAlign + C6(BLAST, Public Domain, 增强宿主库参数) | 无——`bio-primer-validation`仅覆盖热力学不含BLAST | C6, P1 |
| **P3** | **MGB探针Tm计算** `[v12新增]` | TaqMan MGB探针Tm修正——SantaLucia nearest-neighbor计算基础Tm+MGB修饰增量(+15-20°C,公开公式)。MGB探针更短更稳定,Primer3默认算法不包含此修正。⚠️自建Python模块(~2天),公式来源于Thermo Fisher MGB探针设计指南 | P1 | calc_mgb_tm | 自建(SantaLucia参数已有,MGB增量公式公开,Thermo Fisher指南) | 无 | P1 |
| **P4** | **多重荧光通道适配** `[v12新增]` | 单荧光通道复用适配——多探针荧光基团+淬灭基团光谱兼容性验证,确保同一通道内信号可区分。自建荧光数据库(参考Thermo Fisher染料光谱)+Primer3 `calc_heterodimer`交叉二聚体检查+通道分配规则。⚠️自建Python模块(~1周) | P1 | design_multiplex_qpcr | 自建(荧光基团数据库参考Thermo Fisher染料光谱;Primer3 calc_heterodimer) | 无——`bio-qpcr-primers`仅覆盖单重qPCR,不含多重荧光通道适配 | P1 |
| **P5** | **内标引物设计** `[v12新增]` | 自动选择宿主管家基因(β-actin/GAPDH/18S rRNA)保守区,Primer3设计内标引物,BLAST验证与靶标无交叉。输入宿主物种(猪/牛/鸡)→输出内标引物对+验证报告。兽用诊断试剂盒注册要求。⚠️自建Python模块(~1周),管家基因保守区从NCBI RefSeq获取 | P1 | design_internal_control | 自建(宿主参考基因组+Primer3+BLAST。管家基因保守区:NCBI RefSeq) | 无 | P1, P2 |

> ntthal/寡核苷酸热力学/uMelt均为Primer3内置功能已合并为P1。宿主基因组排除→P2增强(加BLAST宿主库参数)。变异耐受性→workflow脚本串联C5(MAFFT)+C21(IQ-TREE)+P1(Primer3 Tm重算),不独立部署。PrimeGen不入——LLM多智能体平台(依赖GPT-4o API+机器人硬件)。检测灵敏度LOD估算标记P2低优先级。

### 流程与依赖

```
C0(病原数据库)→C6b(MMseqs2快速聚类)→C5(MAFFT)→C21(IQ-TREE)→保守区筛选→P1(Primer3:设计+结构+热力学+熔解)
C6(BLAST,含宿主库)+P1→P2(特异性评估)
P1→P3(MGB Tm修正)→P4(多重荧光通道,含交叉二聚体)→P5(内标引物,含与靶标无交叉验证)
→C29(对比报告)→C30(结果解释)
```

---

## 四智能体功能覆盖核验（v12.3）

| 功能维度 | 抗原 | 抗体 | 疫苗 | 引物 | 覆盖工具 |
|------|:---:|:---:|:---:|:---:|------|
| 病原序列检索 | ✅ | — | — | ✅ | **C0(v12新增)** |
| **大规模序列搜索** | ✅ | ✅ | ✅ | ✅ | **C6b(MMseqs2, v12.3新增)** |
| 结构预测 | ✅ | ✅ | ✅ | — | C12(AF3,默认主力), C11(ESMFold2,快速引擎+设计验证) |
| 结构+亲和力联合 | ✅ | ✅ | ✅ | — | **C13b(Boltz-2)** |
| 抗体结构 | — | ✅ | — | — | B1(IgFold), **B1b(ImmuneBuilder,VHH/TCR)** |
| 纳米抗体结构 | — | ✅ | — | — | **B1b(NanoBodyBuilder2)** |
| CDR编号+精修 | — | ✅ | — | — | B2(ANARCI), **B1c(ABlooper)** |
| **已知抗体检索** | — | ✅ | — | — | **B7(SAbDab/OAS, v12.1新增)** |
| 结构搜索 | ✅ | ✅ | — | — | C14(FoldSeek) |
| MHC-I表位 | ✅ | — | — | — | A1(NetMHCpan 4.2+MHCflurry) |
| MHC-II表位 | ✅ | — | — | — | A2(NetMHCIIpan 4.3) |
| B细胞线性表位 | ✅ | — | — | — | A3(BepiPred-3.0) |
| B细胞构象表位 | ✅ | — | — | — | A4(DiscoTope-3.0) |
| 免疫原性评分 | ✅ | — | — | — | A6(IEDB), **A3b(BigMHC)** |
| **病毒逃逸预测** | ✅ | — | — | — | **A3c(EVEscape, v12新增)** |
| 保护性抗原性 | ✅ | — | — | — | A7(VaxiJen) |
| 致敏性筛查 | ✅ | — | — | — | A7(AllerTOP) |
| **保护性抗原预测** | ✅ | — | — | — | **A8(Vaxign-ML, v12.3新增)** |
| 跨膜域选择 | ✅ | — | — | — | A5(DeepTMHMM) |
| 序列设计(通用) | ✅ | ✅ | ✅ | — | C15(ProteinMPNN) |
| 序列设计(抗体CDR) | — | ✅ | — | — | **C15b(IgDesign)** |
| 溶解度优化 | ✅ | — | ✅ | — | C16(SolubleMPNN) |
| 密码子优化 | ✅ | ✅ | ✅ | — | C17(DNA Chisel) |
| Inverse folding(通用) | ✅ | ✅ | ✅ | — | C18(ESM-IF1) |
| Inverse folding(抗体) | — | ✅ | — | — | **C18b(AntiFold)** |
| 从头设计(骨架) | — | ✅ | — | — | B3(RFDiffusion), B6c(FrameFlow,P2) |
| 从头设计(全抗体) | — | ✅ | — | — | B4(RFantibody, Nature 2026) |
| 从头设计(纳米抗体) | — | ✅ | — | — | **B6b(BoltzGen)** |
| 蛋白对接 | ✅ | ✅ | — | — | C22(HADDOCK3) |
| 复合物预测(备选) | ✅ | ✅ | — | — | C13(AF-Multimer,P2) |
| 综合QC+liability | ✅ | ✅ | ✅ | ✅ | C19(Protein QC) |
| 抗体可开发性 | — | ✅ | — | — | B5(TAP+TNP) |
| **多价疫苗设计** | — | — | ✅ | — | **V2(v12新增)** |
| **佐剂推荐** | — | — | ✅ | — | **V3(v12.1新增)** |
| **疫苗平台选择** | — | — | ✅ | — | **V4(v12.1新增)** |
| pH稳定性 | — | ✅ | ✅ | — | C24(PROPKA) |
| 标签暴露 | — | — | ✅ | — | V1(FreeSASA) |
| 糖基化 | — | ✅ | ✅ | — | C25(NetNGlyc) |
| 信号肽 | — | ✅ | ✅ | — | C26(USPNet/SignalP) |
| 引物设计+热力学 | — | — | — | ✅ | P1(Primer3) |
| 引物特异性(含宿主排除) | — | — | — | ✅ | P2(Primer3+BLAST) |
| **MGB探针Tm** | — | — | — | ✅ | **P3(v12新增)** |
| **多重荧光通道** | — | — | — | ✅ | **P4(v12新增)** |
| **内标引物设计** | — | — | — | ✅ | **P5(v12新增)** |
| 保守区筛选 | ✅ | — | — | ✅ | C5(MAFFT)+C21(IQ-TREE) |
| MD模拟 | ✅ | ✅ | ✅ | — | C8(Gromacs) |
| MD分析 | ✅ | ✅ | ✅ | — | C23(MDTraj+MDAnalysis) |
| 相互作用分析 | ✅ | ✅ | ✅ | — | C34(FreeSASA+PRODIGY, P1) |
| **稳定性评估** | ✅ | ✅ | ✅ | — | **C35(FoldX,P1)+C43(ThermoMPNN)+C44(Rosetta,P2)** |
| **突变扫描ΔΔG** | ✅ | ✅ | ✅ | — | **C42(v12.1新增)** |
| 滴度/中和分析 | — | ✅ | ✅ | — | C27/C28 |
| 实验闭环 | ✅ | ✅ | ✅ | ✅ | C31-C44(C41已合并入C9) |

---

## 统计（v12.3）

| 维度 | 数量 |
|------|:---:|
| 通用公共底座 | 49 (C0-C44, 含C6b/C8b/C13b/C15b/C18b; C35拆3+C42/C43/C44新增; C41合并入C9) |
| 抗原独有 | 10 (A1-A8, 含A3b/A3c; A8 v12.3新增) |
| 抗体独有 | 10 (B1-B7, 含B1b/B1c/B6b/B6c/B7; 删除B3b/B5b/B5c) |
| 疫苗独有 | 4 (V1-V4, 新增V2/V3/V4) |
| 引物独有 | 5 (P1-P5, 新增P3/P4/P5) |
| **总计** | **78** |
| v12.2降级 | 7: C8 P0b→P0c, C13b/C14/C21 P0b→P1, B1c P0b→P1, A4 P0c→P1, B7去C14依赖 |
| v12.2升级 | 1: C34 P2→P1 |
| v12.2保留P0b(否决降级) | 4: C8b AMBER, C9 AmberTools, C12 AF3, B7 SAbDab |
| v12.3合并 | 1: C41→C9 (AmberTools精修合并入AmberTools主条目) |
| v12.3新增 | 2: C6b MMseqs2(P1), A8 Vaxign-ML(P1) |
| v12.3引用 | 1: C42追加SKEMPI 2.0数据库引用(不增编号) |
| v12.3否决 | 1: NUPACK (付费订阅+禁止AI/ML+禁止再分发) |

---

*v12.3完成。v12.2基础上:C41合并入C9(同一软件); 新增C6b MMseqs2(大规模序列预筛选); 新增A8 Vaxign-ML(反向疫苗学保护性抗原预测); C42追加SKEMPI 2.0数据库引用; NUPACK否决(付费订阅+禁止AI/ML)。工具总数77→78,依赖链无倒挂。详见 `Skills v12.3修改方案_基于两份外部评审.md`。*
