# Changelog

All notable changes to Repotoire will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0](https://github.com/Zach-hammad/repotoire/compare/v0.3.113...v0.4.0) (2026-03-19)


### Features

* --min-confidence flag and config option for confidence-gated output ([422cf87](https://github.com/Zach-hammad/repotoire/commit/422cf879d8903e429ca677c95ab521815e111cea))
* add --json-sidecar flag for CI actions (single analysis, two outputs) ([0cbe43a](https://github.com/Zach-hammad/repotoire/commit/0cbe43adc8368a4c659b3038600e85ad43fdc333))
* add 3 graph-primitives detectors (SPOF, bridge risk, mutual recursion) ([f454412](https://github.com/Zach-hammad/repotoire/commit/f454412aa0a2925e47e1e487e05433c68064deb6))
* add 5 Phase B GraphQuery methods + CodeGraph wiring ([88bac51](https://github.com/Zach-hammad/repotoire/commit/88bac51a5d8d8c54fd59a86687da7c40eec651ad))
* add AnalysisContext, FileProvider shim, and new Detector trait methods ([35a51cc](https://github.com/Zach-hammad/repotoire/commit/35a51cc10ce1cdc4b8a6520f3f86dacb91226519))
* add AnalysisContext::test() constructor for unit tests ([747f9a5](https://github.com/Zach-hammad/repotoire/commit/747f9a51d27855d683d429a39c801ee489f5e3ab))
* add API surface detection, downgrade security findings on API definitions ([5351e8f](https://github.com/Zach-hammad/repotoire/commit/5351e8fa996beebfff6553811fb43065bffb757a))
* add bypass_postprocessor() trait method for per-detector GBDT bypass ([21f0e7b](https://github.com/Zach-hammad/repotoire/commit/21f0e7bdadc60d3248a636d16f2ccebacda6aa8f))
* add co-change config support + Phase B integration test ([e486154](https://github.com/Zach-hammad/repotoire/commit/e486154448ab484cddd71fdcde0d2565bc96e638))
* add CoChangeMatrix with decay-weighted co-change computation ([5ce324d](https://github.com/Zach-hammad/repotoire/commit/5ce324d8b227c075c97e185a803fe911aa6c0846))
* add CommunityMisplacementDetector (file vs Louvain community) ([0fd7fa2](https://github.com/Zach-hammad/repotoire/commit/0fd7fa2941c3caa91a4789257cbe5aedc8d86732))
* add compute_edge_fingerprint() to GraphStore for topology change detection ([bc3674e](https://github.com/Zach-hammad/repotoire/commit/bc3674e75e1a3642fb0ac8d90c1606a2670d5646))
* add context enrichment to AnalysisContext (Tasks 6-10.5) ([f8b18a0](https://github.com/Zach-hammad/repotoire/commit/f8b18a0df6627e5a459ee879c4c2a1234799ad07))
* add core diff logic with fuzzy finding matching ([df2beaf](https://github.com/Zach-hammad/repotoire/commit/df2beaf15be72738ec76a892a89bbdfd27ca8ed2))
* add DetectorContext — shared callers/callees maps, file contents, class hierarchy in precompute ([599ecf8](https://github.com/Zach-hammad/repotoire/commit/599ecf8f61dd0c8f7ea342c82116901dd6f09e2b))
* add DetectorInit, RegisteredDetector trait, and registry infrastructure ([b79a6a6](https://github.com/Zach-hammad/repotoire/commit/b79a6a62758e036254cf35dc897d3f5a594f59a5))
* add DetectorScope enum and detector_scope() trait method ([a6acce0](https://github.com/Zach-hammad/repotoire/commit/a6acce0cc3bd595e9b4dfc215aa0c5cf39e7e200))
* add diff output formatters (text, JSON, SARIF) ([84e0dad](https://github.com/Zach-hammad/repotoire/commit/84e0dade6985427f34a4ecb95b7abf296cbd4c6e))
* add e2e validation script for self-analysis and real-world projects ([02f7eab](https://github.com/Zach-hammad/repotoire/commit/02f7eabf7ed721c8e82542707bc5058960488423))
* add engine persistence (save/load) and rewire MCP to AnalysisEngine ([43e185f](https://github.com/Zach-hammad/repotoire/commit/43e185f7899a26652e002baddbbdecd4a272636d))
* add export annotations to C/C++ parsers, complete C++ class method extraction ([1982e5f](https://github.com/Zach-hammad/repotoire/commit/1982e5fe2428823f39175cc6aee10b84d2fc68fe))
* add file_extensions/content_requirements to 12 taint detectors ([c324eec](https://github.com/Zach-hammad/repotoire/commit/c324eec7d1af9c9e5ce9f9030502d026edb6ddc6))
* add file_extensions/content_requirements to all remaining detectors ([188a323](https://github.com/Zach-hammad/repotoire/commit/188a3231023627ab1be1444965d41427425eca02))
* add FileIndex with lazy pre-computed content ([6e473b6](https://github.com/Zach-hammad/repotoire/commit/6e473b6a3c89701bf10f5bd34b9cae0d7cb8dc20))
* add get_all_edges() to GraphStore for determinism testing ([f81cd70](https://github.com/Zach-hammad/repotoire/commit/f81cd707ec6a0d32b674c148072fcc35bbfd9248))
* add GraphPrimitives accessor methods to CodeGraph ([0ac4b54](https://github.com/Zach-hammad/repotoire/commit/0ac4b54f673bc8e234058341b312d199fae43338))
* add GraphPrimitives struct with empty compute() ([0b0514c](https://github.com/Zach-hammad/repotoire/commit/0b0514c37df446187560da8b4f637637dab673a9))
* add GraphWide detector_scope() overrides for cross-file detectors ([1ae67a2](https://github.com/Zach-hammad/repotoire/commit/1ae67a216666f6a0adc0a2d05ac42de474288f6d))
* add HiddenCouplingDetector (co-change without structural edge) ([304850d](https://github.com/Zach-hammad/repotoire/commit/304850d6dc7ead98c001f557b39502954635797a))
* add NodeIndex-based GraphQuery API and migrate key consumers (Phase C) ([ec1f57a](https://github.com/Zach-hammad/repotoire/commit/ec1f57ab0446a3c7106346678b830c73b4612f60))
* add PageRankDriftDetector (static vs weighted importance divergence) ([a9a556a](https://github.com/Zach-hammad/repotoire/commit/a9a556a8701f40ef4ba3d766c8d74d1e8410f88a))
* add per-detector timing report to --timings flag ([651eaf7](https://github.com/Zach-hammad/repotoire/commit/651eaf753e8be0bb005c1bc0036cfafe3b29a453))
* add persistent graph cache — bincode save/load with index rebuild ([e816b9f](https://github.com/Zach-hammad/repotoire/commit/e816b9f64064cca01aa9d0d3c3a6f2a2d2e386f3))
* add Phase B fields to GraphPrimitives + weighted overlay stub ([c91b3cf](https://github.com/Zach-hammad/repotoire/commit/c91b3cf88d0fdfa9b662486094491d208017d3c1))
* add PrecomputedAnalysis alias and to_context() on GdPrecomputed ([2da4723](https://github.com/Zach-hammad/repotoire/commit/2da47232d9e78d9c9f31d32320eed1f171eb436a))
* add RegisteredDetector impl to 81 detectors and populate DETECTOR_FACTORIES ([5df3d61](https://github.com/Zach-hammad/repotoire/commit/5df3d612fd256eee56cb6bf11f8ade731eee7866))
* add remove_file_entities() for graph delta patching ([861349a](https://github.com/Zach-hammad/repotoire/commit/861349a42a0760ed58eb09ca32ab9ef7c8ea0693))
* add repotoire diff CLI command ([289f6ba](https://github.com/Zach-hammad/repotoire/commit/289f6ba8fedcc57fed308acf578ddd47de40eacb))
* add repotoire_diff MCP tool ([be2b3db](https://github.com/Zach-hammad/repotoire/commit/be2b3db2a8ac341475b3ae6e5e995b071d112f63))
* add shared AST fingerprinting utility for AI detectors ([2ade359](https://github.com/Zach-hammad/repotoire/commit/2ade359ef6f6bb6054fe6c3da765f2d2cccee0b7))
* add standalone detector runner with pure functions ([23a07de](https://github.com/Zach-hammad/repotoire/commit/23a07de060be54eb9ef1e596616aeed6cf6d0153))
* add TemporalBottleneckDetector (change-propagation critical paths) ([85995ca](https://github.com/Zach-hammad/repotoire/commit/85995ca03a3e2cf206d97f3471937508221f31c0))
* AIBoilerplateDetector uses tree-sitter AST fingerprinting for structural clustering ([574d940](https://github.com/Zach-hammad/repotoire/commit/574d9403f6fe479cb629287bf88e52cf82f94a6f))
* AIChurnDetector uses git2 for function-level churn analysis ([4a3261b](https://github.com/Zach-hammad/repotoire/commit/4a3261bca2ed25ea66acf31d7073bf510da7e5cb))
* AIDuplicateBlockDetector uses tree-sitter normalized AST for cross-file duplicate detection ([8eb0085](https://github.com/Zach-hammad/repotoire/commit/8eb008511706dffc7cadc307f006ea2b2641d128))
* AINamingPatternDetector analyzes variable names in function bodies ([d635e32](https://github.com/Zach-hammad/repotoire/commit/d635e32d6b9a29443a4baf725cb964c33a9b19d0))
* **cache:** add cross-file value dependency tracking to incremental cache ([3e2f251](https://github.com/Zach-hammad/repotoire/commit/3e2f2512c391b9ffd4f42e258d9e7e82b880f66d))
* CLI analyze uses persisted AnalysisSession for incremental re-analysis ([2e46373](https://github.com/Zach-hammad/repotoire/commit/2e46373c331e6463946658fc192955bc7f573b8d))
* complete detector registry with all 100 detectors in DETECTOR_FACTORIES ([549764d](https://github.com/Zach-hammad/repotoire/commit/549764dbdb8d5285f39d7bd8f9693f46fc0f6034))
* confidence display in all 5 output formats ([e15cfaf](https://github.com/Zach-hammad/repotoire/commit/e15cfaf9102bfa7b2b8503990fac56d6275e2548))
* confidence enrichment pipeline with content classifier and multi-detector signals ([4c82ae4](https://github.com/Zach-hammad/repotoire/commit/4c82ae494cdeb86aefd7701ecea24b22fa712999))
* create engine module with public API types and stage stubs ([57141ec](https://github.com/Zach-hammad/repotoire/commit/57141ecec6f805d9518d5e4423baa4c947ba6994))
* detect callback function arguments as call edges in JS/TS ([a1a18b6](https://github.com/Zach-hammad/repotoire/commit/a1a18b68a091419453d3301d2039b6d4e275aab1))
* detect exported functions/classes in all parsers via annotation ([781da9e](https://github.com/Zach-hammad/repotoire/commit/781da9e3406316119f902f07edc9541fd5f05d7e))
* emit synthetic Calls edges for decorated functions ([2a80236](https://github.com/Zach-hammad/repotoire/commit/2a80236e4f10a260bb43d1cab2f705f826396205))
* emit trait implementation Inherits edges in graph ([88d3266](https://github.com/Zach-hammad/repotoire/commit/88d32669f1b9f1d8c5fda4d637e0904aabe0e02c))
* **engine:** implement AnalysisEngine with cold analysis path ([7910435](https://github.com/Zach-hammad/repotoire/commit/79104356e2b3dd5500ed368e76a6c29f470cc33a))
* **engine:** implement incremental analysis path with graph patching ([4ad9fb9](https://github.com/Zach-hammad/repotoire/commit/4ad9fb9bf8dd7f282e463dffb33e8814f5d8fef7))
* extend ContentFlags from 2 to 16 categories ([77bcfe0](https://github.com/Zach-hammad/repotoire/commit/77bcfe0745519d87be2b521e74df96cca3886255))
* extend GraphQuery trait with 10 primitive query methods ([0181791](https://github.com/Zach-hammad/repotoire/commit/0181791074899ac3e3a8b834d44953a6cba5e5eb))
* extract JS/TS decorator names into annotations ([20b3d9b](https://github.com/Zach-hammad/repotoire/commit/20b3d9b1ea9a83ee3662af22b531dae3ff993300))
* extract Python decorator names into function/class annotations ([d69efa9](https://github.com/Zach-hammad/repotoire/commit/d69efa93c8d605d115e8e7532578a5bf0ac7c8ee))
* extract Rust #[attribute] into function/class annotations ([4aaed87](https://github.com/Zach-hammad/repotoire/commit/4aaed870b70e3e3e8d9d2019e4cb49e2f261c175))
* extract shared cross-line user-input helper with tests ([63b2c6f](https://github.com/Zach-hammad/repotoire/commit/63b2c6f256ea6f1d2a9ed09f97d380c9e0e78585))
* file-level suppression (repotoire:ignore-file) and auto-suppress detector test fixtures ([9f5e370](https://github.com/Zach-hammad/repotoire/commit/9f5e370cfa23bbf622063cdbb72dc455650f2921))
* graph-verified semantic overlap for clone detection ([27e7094](https://github.com/Zach-hammad/repotoire/commit/27e709498ede8dde6175b8a2972bb567921b479e))
* harden analysis pipeline with symlink/traversal/size protection ([c39fc72](https://github.com/Zach-hammad/repotoire/commit/c39fc72ba164710bd9723a2a450d6780b5700055))
* implement 7 parser features (doc comments, annotations, React patterns) ([9b380b2](https://github.com/Zach-hammad/repotoire/commit/9b380b27e8221266404ba07548dd2e2cc09cd222))
* implement 8 stage wrappers for analysis pipeline rewrite ([f7b3c79](https://github.com/Zach-hammad/repotoire/commit/f7b3c79b61d707f1dd5b95e55934786f2e055407))
* implement all 6 graph primitive algorithms with rayon parallelism ([0ebfb21](https://github.com/Zach-hammad/repotoire/commit/0ebfb21e5a9cf5a3d192b709042b386c4d76a6e6))
* implement Builder/Frozen graph architecture (Phase A) ([9329ae8](https://github.com/Zach-hammad/repotoire/commit/9329ae8fc674136a0a51d7e96ff97befc388c0f0))
* implement Dijkstra-based weighted betweenness centrality ([ea57380](https://github.com/Zach-hammad/repotoire/commit/ea57380a4c97f01ea98e75582e13e5160f91fa06))
* implement GraphQuery primitive overrides for CodeGraph and Arc&lt;CodeGraph&gt; ([5767e48](https://github.com/Zach-hammad/repotoire/commit/5767e485f8e2d60ef8309536ca8c1c6f5269b8ee))
* implement Louvain community detection algorithm ([6f37ba8](https://github.com/Zach-hammad/repotoire/commit/6f37ba8c92fbdeb180d1b3ad827f2870eb8b4912))
* implement weighted overlay graph builder ([c6df966](https://github.com/Zach-hammad/repotoire/commit/c6df96643b2f36a9facc4d51de308d4bd2a86076))
* implement weighted PageRank algorithm ([1cdf141](https://github.com/Zach-hammad/repotoire/commit/1cdf141d620ad109cb916c116492736f0dc7ba04))
* improve CLI help text with examples and descriptions ([6b2f7bd](https://github.com/Zach-hammad/repotoire/commit/6b2f7bd7dd3bb5c625afc20ff61d65cae28e6012))
* integrate AnalysisSession into MCP server for incremental analysis ([269ebba](https://github.com/Zach-hammad/repotoire/commit/269ebba830c603f29f5540335713032b8a99ca31))
* mandatory default confidence on all findings by category ([ad4265e](https://github.com/Zach-hammad/repotoire/commit/ad4265e169f044686e69e8c99677f60b55348180))
* **mcp:** add AI tool handlers (search_code, ask, generate_fix) ([149c100](https://github.com/Zach-hammad/repotoire/commit/149c100ae0e4818274185c3a389d53f1e49034b9))
* **mcp:** add analysis tool handlers (analyze, get_findings, get_hotspots) ([e38e80d](https://github.com/Zach-hammad/repotoire/commit/e38e80da030d217c934069b9889415481f5e5ffb))
* **mcp:** add evolution tool handler (query_evolution -- 7 temporal query types) ([f6ee810](https://github.com/Zach-hammad/repotoire/commit/f6ee810cd8b1587232f0406d2e1ae4ce78dae762))
* **mcp:** add file tool handlers (get_file, get_architecture, list_detectors) ([65a61a6](https://github.com/Zach-hammad/repotoire/commit/65a61a61dbc442ba72863e131cb433156c283320))
* **mcp:** add graph tool handlers (query_graph, trace_dependencies, analyze_impact) ([b035add](https://github.com/Zach-hammad/repotoire/commit/b035addfdecc2fc4e4fe53aa2a1244402b563be5))
* **mcp:** add parameter structs with schemars JSON Schema generation ([a79634a](https://github.com/Zach-hammad/repotoire/commit/a79634a3b45f355cf09ad2174c2c8eb6e57c4a25))
* **mcp:** implement rmcp ServerHandler with 13 tools via tool_router ([509fec7](https://github.com/Zach-hammad/repotoire/commit/509fec7724c3b4fe7577e355965c2da4b6eecc68))
* **mcp:** wire rmcp server with stdio + HTTP transports and CLI --http-port flag ([b444352](https://github.com/Zach-hammad/repotoire/commit/b444352cffc1e8d6d628d8d25c560031005b099c))
* ML classifier pipeline — GBDT model, 28 evidence-backed features, debt scoring ([d0c5f19](https://github.com/Zach-hammad/repotoire/commit/d0c5f19f718525f2741217343b9b9a1b4898ef31))
* multi-dimensional Rust type evaluation in LazyClass detector ([f355354](https://github.com/Zach-hammad/repotoire/commit/f35535481ae33b7025e048c089a442b8a5185cee))
* opt 22 security detectors into GBDT bypass ([d261a1c](https://github.com/Zach-hammad/repotoire/commit/d261a1c806cae4cee00ee558c72e8d3be0766385))
* precision scoring harness for benchmark suite ([fdcc04c](https://github.com/Zach-hammad/repotoire/commit/fdcc04ce75895fd9de54783c8e1af1e77e90b17a))
* **predictive:** add HierarchicalSurprisalDetector replacing flat surprisal ([55cfcfa](https://github.com/Zach-hammad/repotoire/commit/55cfcfacf91c03c86185748d73b4da0df75a8aa1))
* **predictive:** add L1 token scorer and L2 structural scorer ([84faea9](https://github.com/Zach-hammad/repotoire/commit/84faea900142b0f6fb2eddb17618ea0c0f06ef48))
* **predictive:** add L1.5 dependency-chain surprisal scorer ([6769332](https://github.com/Zach-hammad/repotoire/commit/67693323fa076e80466e9eb408bb43ebe4f34e40))
* **predictive:** add L3 relational scorer with per-edge-type node2vec ([797e93b](https://github.com/Zach-hammad/repotoire/commit/797e93b2a88edbbfa02c8f370699797aa40d79ec))
* **predictive:** add L4 architectural module-level scorer ([2ca39d1](https://github.com/Zach-hammad/repotoire/commit/2ca39d102bf6407fe8f1cb3ccf52962e1f9530a5))
* **predictive:** add module skeleton and compound scoring ([785ed50](https://github.com/Zach-hammad/repotoire/commit/785ed50afda7d56c032885b68a688ac883c74901))
* **predictive:** port node2vec + word2vec from repotoire-fast ([d13b2e6](https://github.com/Zach-hammad/repotoire/commit/d13b2e6cf57113693eb84061251dce9a3e1e51e6))
* **predictive:** wire up PredictiveCodingEngine orchestrating all 5 levels ([f266e66](https://github.com/Zach-hammad/repotoire/commit/f266e66ba28513630c4c242dcba1f1cfd69d7910))
* rearchitect 8 top detectors with context-aware FP reduction ([1a98737](https://github.com/Zach-hammad/repotoire/commit/1a987373a41c5cbf734db31609043db640aa2179))
* refactor UnreachableCodeDetector — remove dead function overlap, scope-aware return detection ([e6448a6](https://github.com/Zach-hammad/repotoire/commit/e6448a6463093e08479eb73f21c8bf91c6ec14c6))
* replace DeadCodeDetector pattern lists with graph flags and role-based gating ([f7b6295](https://github.com/Zach-hammad/repotoire/commit/f7b62952c0c0cfb7b86383e8288b160637ff67c9))
* replace LazyClass EXCLUDE_PATTERNS with role analysis and adaptive thresholds ([5211f76](https://github.com/Zach-hammad/repotoire/commit/5211f760c1661b0b909efc9e2960c98032920155))
* rewire CLI analyze to use AnalysisEngine via run_engine() ([d94d97f](https://github.com/Zach-hammad/repotoire/commit/d94d97f8196b591fcb61949258b2ee86a80f1943))
* rewrite AIMissingTestsDetector with graph-based test reachability ([3f9c25f](https://github.com/Zach-hammad/repotoire/commit/3f9c25faa946ecaeb28b8094ceb9fa9464bfd3b0))
* rewrite DeadStoreDetector with ValueStore-based analysis ([92bd41c](https://github.com/Zach-hammad/repotoire/commit/92bd41c339cf344bd4190d5db742c0f741e0c194))
* rewrite watch mode to use AnalysisSession for incremental updates ([3cb169c](https://github.com/Zach-hammad/repotoire/commit/3cb169c9963076167c6946b7970da28759230c05))
* role-aware ShotgunSurgery thresholds with ContextHMM utility detection ([85a0451](https://github.com/Zach-hammad/repotoire/commit/85a0451e3bcfabd7e3b54bac73fed69a411fefae))
* seed model training pipeline — export-training flag, bootstrap labels, embedded GBDT ([a900686](https://github.com/Zach-hammad/repotoire/commit/a90068669e5e3a8705a79fca522efc884bac43e0))
* selective normalization in AST fingerprinting for clone detection ([72cbc97](https://github.com/Zach-hammad/repotoire/commit/72cbc97d7ee9273c32e7a4e75e2d47961ebab30c))
* session persistence optimization and detector skip filtering ([292f01c](https://github.com/Zach-hammad/repotoire/commit/292f01ce934a0bd0263b6cc8b7da605b0074d5f2))
* size-adaptive similarity threshold for clone detection ([6d531bf](https://github.com/Zach-hammad/repotoire/commit/6d531bf7da1b877d15ea50f200aa97485989d68e))
* skip single-assignment module-scope vars (effectively const) ([b238200](https://github.com/Zach-hammad/repotoire/commit/b238200a2f38c7137881f4a24b1c68fe9fffa05c))
* upgrade top 8 detectors with context-based FP reduction ([4427532](https://github.com/Zach-hammad/repotoire/commit/4427532978baa821e858e05e4ebafcbb2f973be7))
* **values:** add language configs for all 9 supported languages ([4b5996c](https://github.com/Zach-hammad/repotoire/commit/4b5996c4c7e6cb01801692921ad2ca336520ccfe))
* **values:** add SymbolicValue, LiteralValue, BinOp core types with guard rails ([e209439](https://github.com/Zach-hammad/repotoire/commit/e20943902b4fc32bbf1d4e62db7527457c9ca8a3))
* **values:** add ValueStore with query API, Assignment, RawParseValues types ([e7a95e8](https://github.com/Zach-hammad/repotoire/commit/e7a95e84238223ef74b3d9702a36232eba190baa))
* **values:** build ValueStore during graph construction with Variable nodes and propagation ([e1afd50](https://github.com/Zach-hammad/repotoire/commit/e1afd50f8fc189a61e952a0141c39ce7588ef7a5))
* **values:** cross-function propagation with cycle detection and depth limits ([bd262b3](https://github.com/Zach-hammad/repotoire/commit/bd262b35236c36dc8c14bc7fe74bb748cf6e55e1))
* **values:** integrate value extraction into parse phase, reuse tree-sitter tree ([b3a0060](https://github.com/Zach-hammad/repotoire/commit/b3a0060422ffb3d5e601afefd98fabdbfd9d9218))
* **values:** table-driven extraction framework with Python config and node_to_symbolic ([511013b](https://github.com/Zach-hammad/repotoire/commit/511013bfa8c982f9f17dce08997fedfbdf99add5))
* **values:** thread ValueStore through pipeline to detectors via DetectorContext ([60df4c4](https://github.com/Zach-hammad/repotoire/commit/60df4c462d43a8c814c7be46dd086256e5da067b))
* wire CoChangeMatrix through git enrich → freeze pipeline ([ca232c9](https://github.com/Zach-hammad/repotoire/commit/ca232c9ac64d82052d1a8267f45075f62f5104be))
* wire ContextHMM + ThresholdResolver into AnalysisContext (Phase 0) ([9508730](https://github.com/Zach-hammad/repotoire/commit/9508730570a4c279ec3704b8aec5260857998c18))
* wire FileIndex into engine pipeline, call detect_ctx() ([4b707d1](https://github.com/Zach-hammad/repotoire/commit/4b707d18da7caea8b7aa1273347d39ec4a5ac1a7))
* wire GraphPrimitives into GraphIndexes::build() ([600d6a2](https://github.com/Zach-hammad/repotoire/commit/600d6a2615811aa7e2199d12a80ab2f2de9c839a))
* wire persistent graph cache into analyze pipeline — load on incremental, save after build ([29a11f6](https://github.com/Zach-hammad/repotoire/commit/29a11f6dcc60e575596a196be99be1cbd7306d96))
* wire pipeline to Builder/Frozen CodeGraph flow (Phase B) ([d31706f](https://github.com/Zach-hammad/repotoire/commit/d31706f6295601b00271aba66a0c1917d1430bb7))
* wire ProjectType into scoring bonuses for framework-aware scoring ([33ef8f1](https://github.com/Zach-hammad/repotoire/commit/33ef8f19ad790cb0a48a26d04ca659f71fd5aa24))


### Bug Fixes

* 10 detector bugs from full audit — DataClumps, FP reduction, regex tightening ([7525184](https://github.com/Zach-hammad/repotoire/commit/7525184d08dd649474e2de5520dcf8f15a636314))
* 7 detector bugs from audit round 2 — ReDoS, pickle masking, path matching ([4c5907c](https://github.com/Zach-hammad/repotoire/commit/4c5907c3e05226a59c1bdeaa855161326f470148))
* add abstract and partial class name-based exclusions for Java/C# LazyClass FP reduction ([cfef097](https://github.com/Zach-hammad/repotoire/commit/cfef097d5458fbdc1eb382da7e914422650cdc85))
* add build_legacy fallback in ClassContextBuilder for GraphStore tests ([222fece](https://github.com/Zach-hammad/repotoire/commit/222fece1ad40f6c5d326ba8148ff0885c8f22ac6))
* add export annotations to Java, C#, and TypeScript class methods ([3ea57f5](https://github.com/Zach-hammad/repotoire/commit/3ea57f50a1ea4e0d3e74ea89468cdd1ce3a02dd6))
* add inline suppression support to surprisal detector ([fcfa370](https://github.com/Zach-hammad/repotoire/commit/fcfa3707bbbc1db9b8e7e75241fae812e2c1e202))
* add language-aware filtering to taint analysis (4 → 0 SQLInjection FPs) ([d1abe30](https://github.com/Zach-hammad/repotoire/commit/d1abe3077c8fb6a197f94b0a5ad13b692a8f8d3a))
* add shallow-clone guard + fix test memory leak in co_change ([97e0860](https://github.com/Zach-hammad/repotoire/commit/97e0860fbcaed1a5ac1821a6c11188fc81fd12c0))
* add threshold_metadata to CachedFinding for lossless cache round-trip ([27b85de](https://github.com/Zach-hammad/repotoire/commit/27b85deb8c5bb882170f6a6440b3c1fb84ccf355))
* address all 14 code audit findings from 5-reviewer analysis ([8716c6e](https://github.com/Zach-hammad/repotoire/commit/8716c6ecf8ff919382881e4427459fd938add66e))
* address code review — atomic cache writes, drop RwLock before serialize, guard redundant saves ([863cdbd](https://github.com/Zach-hammad/repotoire/commit/863cdbd634a7d8da106f0e9d918fd7e29b811499))
* address code review — fallback DetectorContext injection, unit tests, capacity pre-sizing ([71c3a59](https://github.com/Zach-hammad/repotoire/commit/71c3a59d0deedcbcce9f2778e0763e139995b776))
* address code review findings from compact node migration ([5ba3302](https://github.com/Zach-hammad/repotoire/commit/5ba3302abfc59b01f2f84728dae305a39b29a145))
* address Phase B code review — dangling mass, unwrap, determinism, config wiring ([530bf78](https://github.com/Zach-hammad/repotoire/commit/530bf78069283f69dd52a1d983c95cc137188c52))
* address second code review findings ([09d144c](https://github.com/Zach-hammad/repotoire/commit/09d144c9134f4f9acd123b2caed4c502ec6d4adb))
* aggressively tune Phase B thresholds to reduce noise ([afe9b7d](https://github.com/Zach-hammad/repotoire/commit/afe9b7d44f65a0c481900a90d02aabce7feebe5f))
* AIComplexitySpikeDetector lower minimum complexity threshold from 20 to 10 ([aee3eac](https://github.com/Zach-hammad/repotoire/commit/aee3eacf96480aeadd4ced5356d1bf1f5dde9472))
* AIMissingTestsDetector lower LOC threshold and skip framework boilerplate ([e436b09](https://github.com/Zach-hammad/repotoire/commit/e436b09b7ff0cdfd32b6e68b61cb362d250726e3))
* AIMissingTestsDetector use word-boundary test matching instead of substring ([d2fd4f2](https://github.com/Zach-hammad/repotoire/commit/d2fd4f273e547fec215a9ac03e6dc37af3af6938))
* align extension loops with file_extensions() for 3 detectors ([5cfa75e](https://github.com/Zach-hammad/repotoire/commit/5cfa75e41297ab42d0e6b2fdafcaca7dd3cebb74))
* apply canonical sort to speculative path and paginate output ([9d778d6](https://github.com/Zach-hammad/repotoire/commit/9d778d6fa7daf85566b8f770ff15c42e953291e6))
* CallbackHellDetector filters object methods and tracks brace-depth-aware nesting ([e3e3624](https://github.com/Zach-hammad/repotoire/commit/e3e3624d4f82bfd8b6f98f3ce45965573d1f1015))
* canonical finding sort for deterministic truncation ([5f41d37](https://github.com/Zach-hammad/repotoire/commit/5f41d373dc5693ca7923dca0c0c3eeadede3524e))
* cap boilerplate cluster size to prevent galaxy clusters ([ff54c2d](https://github.com/Zach-hammad/repotoire/commit/ff54c2ddcf89e49cc6a8343ce7b5994a5a8351f6))
* close 8 detector language-support gaps ([7caeb5f](https://github.com/Zach-hammad/repotoire/commit/7caeb5fe54c563ca1ea439e62dade321c8d644bd))
* CommandInjectionDetector — use cross-line context for Go and Java exec ([8b5db03](https://github.com/Zach-hammad/repotoire/commit/8b5db036815c2d86d789a41ca8a4fd96a5ba972f))
* **compact:** continue CompactNode migration - resolve StrKey Display and type errors ([3ceab59](https://github.com/Zach-hammad/repotoire/commit/3ceab595d5903b2c1e9c607c57507b4093ae509c))
* **compact:** continue fixing StrKey migration errors (56 remaining) ([0e77f27](https://github.com/Zach-hammad/repotoire/commit/0e77f277e4bd6a6f49debf2d822ade8253f13a0f))
* **compact:** resolve all remaining StrKey compilation errors (41 → 0) ([b2eb4ef](https://github.com/Zach-hammad/repotoire/commit/b2eb4ef183821c8804ec54474b6b5b435b178a7a))
* **compact:** resolve all test compilation errors and failures (1166 pass, 0 fail) ([78ecd5f](https://github.com/Zach-hammad/repotoire/commit/78ecd5fe5cc0525f487d82c0a90159acbfd3d2dd))
* continue CompactNode migration for consumers and tests ([48d2bdb](https://github.com/Zach-hammad/repotoire/commit/48d2bdbf58ef3ccf0faa83a2994ba8fd57136a6b))
* convert code-like comments to doc comment in missing_await detector ([adbb258](https://github.com/Zach-hammad/repotoire/commit/adbb2585fde0d423ac9b1b403917638961d01775))
* correct binary path in validation script (cli/target, not workspace target) ([d9d2196](https://github.com/Zach-hammad/repotoire/commit/d9d2196f3a010d52b5d58ca1affe72c3abb99cfe))
* CorsMisconfigDetector — match on raw content, validate with masked to handle '*' ([2b2b865](https://github.com/Zach-hammad/repotoire/commit/2b2b865c5438db4b5a43818d27a11c0408641562))
* dead code exemptions for test modules, benchmarks, pub API surface ([06bed3a](https://github.com/Zach-hammad/repotoire/commit/06bed3aab36d4da30d444ef5dd3f6b88afb381df))
* DebugCodeDetector skips info utilities (ogrinfo) and print() in except blocks ([92cdbc8](https://github.com/Zach-hammad/repotoire/commit/92cdbc88b814b1a49b3efe1d9311ac2e0b229ca6))
* DebugCodeDetector word boundary, remove debug=True, add verbosity guard and management paths ([88cf55a](https://github.com/Zach-hammad/repotoire/commit/88cf55a464a5e903a9df8422448e0e387bae1baa))
* deduplicate get_node call in regex_in_loop fast path ([ebcf30a](https://github.com/Zach-hammad/repotoire/commit/ebcf30a335e6090a8dca2ababa4506b454290b83))
* default JSON/SARIF output to all findings instead of paginated 20 ([8925daf](https://github.com/Zach-hammad/repotoire/commit/8925dafd651f7ca8ba92a8ca06da130390d613e5))
* derive Ord on DeferredEdgeKind for total sort order, update docs ([67c4651](https://github.com/Zach-hammad/repotoire/commit/67c46510583f4bfbe09081b6d640cb9b6a8ef669))
* deterministic voting engine with BTreeMap ([fab7fec](https://github.com/Zach-hammad/repotoire/commit/fab7fec21f4a44adfff29cfc3293121dbe8377c4))
* DjangoSecurityDetector expands ORM path exclusions (constraints, fields, query, postgres) ([9ef6610](https://github.com/Zach-hammad/repotoire/commit/9ef66101203d4ddc542d27a65c9b20cc2ea6654b))
* DjangoSecurityDetector skips CSRF decorator definitions, comments, and management commands ([231898f](https://github.com/Zach-hammad/repotoire/commit/231898f66d3fd1d2c5578d44b8864a3a2f26e997))
* DjangoSecurityDetector skips ORM/database backend paths for raw SQL rule ([5266205](https://github.com/Zach-hammad/repotoire/commit/52662050e4b33e748c8f1f032617c6c97b6b79b7))
* eliminate 19 medium findings — large files, nesting FPs, circular dep ([475efaa](https://github.com/Zach-hammad/repotoire/commit/475efaaf5783ebd658c6a77c5e01a03442dcfea8))
* eliminate all 17 deep nesting findings across 21 files (17 → 0) ([e17e758](https://github.com/Zach-hammad/repotoire/commit/e17e758a2436a51eae962719d7759144aef58905))
* eliminate dead code detector false positives (100 → 3 findings, 0% FP rate) ([e133234](https://github.com/Zach-hammad/repotoire/commit/e13323411f252a486aa316c8d1c132e36923f1ba))
* eliminate FPs in UnreachableCode (49→0) and AIDuplicate (9 test FPs removed) ([68c6687](https://github.com/Zach-hammad/repotoire/commit/68c66871c281c87372eef6c0fae66df2a7166c08))
* eliminate method name conflation in graph cross-file resolution ([ec33407](https://github.com/Zach-hammad/repotoire/commit/ec33407225fd3de3c5ff9a76e2bb32f0dd5f3c0a))
* EmptyCatchDetector inverts idiom logic — any named exception is Low, only broad catches get Medium/High ([492f096](https://github.com/Zach-hammad/repotoire/commit/492f096f9def59b7db8398f7f30c662c0ac763ae))
* EmptyCatchDetector skips cleanup methods, import probing, and safe single-line probes ([3700f84](https://github.com/Zach-hammad/repotoire/commit/3700f84bbac2bd970199e1c5c0c86d262b84307e))
* EvalDetector skips .eval() methods, fixes framework path matching, skips safe subprocess ([4d09853](https://github.com/Zach-hammad/repotoire/commit/4d09853581171478d5076b63827f9f89d4dbc8e9))
* extract Python class methods as full Function entries, add pub detection to Rust impl methods ([04be2cc](https://github.com/Zach-hammad/repotoire/commit/04be2cc2cfe27b2591752fc710a45b4d316a37f5))
* findings deserialization crash — serialize threshold_metadata as {} not null ([059d8ed](https://github.com/Zach-hammad/repotoire/commit/059d8ed08c90ead41f4be3e35dd154eea567b3f7))
* GeneratorMisuseDetector handles yield-from, string-aware yield, standalone [@contextmanager](https://github.com/contextmanager) ([a961036](https://github.com/Zach-hammad/repotoire/commit/a9610368be35f12ae889d14fb6409d37fb899139))
* GeneratorMisuseDetector skips polymorphic interface methods and detects cross-file lazy consumption ([e3e5f87](https://github.com/Zach-hammad/repotoire/commit/e3e5f87cc8d231efd79c61405031f6a4559d345d))
* GlobalVariablesDetector skips docstrings and deduplicates per (file, variable) ([cd42027](https://github.com/Zach-hammad/repotoire/commit/cd4202722699199321636be8f18f0d5cad3d18dc))
* GodClassDetector handles relative test paths (tests/ without leading slash) ([060c19e](https://github.com/Zach-hammad/repotoire/commit/060c19ed28b13f86b1fe7ede7817b3cfc09b6290))
* GodClassDetector skips test classes and recognizes framework core suffixes ([08bfc9f](https://github.com/Zach-hammad/repotoire/commit/08bfc9fe4883b5fd2783efc44d33273b802a5a8e))
* increase thread stack size to 8MB — prevent stack overflow on deeply nested C/C++ files ([e3bec4f](https://github.com/Zach-hammad/repotoire/commit/e3bec4f163412d65fa4a0d8769766c111ada3731))
* InsecureCookieDetector removes .cookies[] regex and widens context window to +15 ([f945e60](https://github.com/Zach-hammad/repotoire/commit/f945e602760b6394f1eed8f61288f066bd585cd9))
* InsecureCryptoDetector Java + systemic masked_content fallback ([881f772](https://github.com/Zach-hammad/repotoire/commit/881f772c1082f005015076f1cf2547231a282ced))
* InsecureCryptoDetector recognizes usedforsecurity=False, skips class/def, fixes test-path bug ([3ebef8c](https://github.com/Zach-hammad/repotoire/commit/3ebef8cbdff3b24bac26eb27d421627cb01ced14))
* InsecureCryptoDetector skips cache key hashing and release scripts ([b4d9da6](https://github.com/Zach-hammad/repotoire/commit/b4d9da6384d8710b36f83f14b12ff81d1d1b9eab))
* language-specific LazyClass exclusions for Go, Java, C#, Python ([3fbb655](https://github.com/Zach-hammad/repotoire/commit/3fbb65529ab9663d7e32d572193720a3c996471b))
* LazyClassDetector adds ORM Lookup/Transform/Field patterns and test path exclusion ([4afac67](https://github.com/Zach-hammad/repotoire/commit/4afac676606615e7427605c56934cf2d0037744f))
* LazyClassDetector handles relative test paths (tests/ without leading slash) ([fdd9399](https://github.com/Zach-hammad/repotoire/commit/fdd9399ae478c69eb58d363040ac05cbe9412ce4))
* make determinism test tolerant of rayon non-determinism ([bd373cf](https://github.com/Zach-hammad/repotoire/commit/bd373cf9fbfa45765546168e02151c7485dae4a6))
* make diff instant by comparing cached findings instead of re-analyzing ([676a668](https://github.com/Zach-hammad/repotoire/commit/676a668c14bea9ad4f40cd69c5181e8eba4e9be5))
* MCP handlers return errors instead of silent empty results ([de9b925](https://github.com/Zach-hammad/repotoire/commit/de9b925ff2bf63cb3e63cd7415a5054e761d69f5))
* overhaul InsecureDeserializeDetector — Java support, cross-line context, GBDT bypass ([b72fd78](https://github.com/Zach-hammad/repotoire/commit/b72fd7835601bcbbabb4546fb59bc70bb63a91ca))
* PathTraversalDetector narrows path_join regex, replaces broad request. with specific accessors ([249f57d](https://github.com/Zach-hammad/repotoire/commit/249f57d77d54fe2f004f1619be7a8190be853880))
* PickleDeserializationDetector skips cache/backends/ and sessions/backends/ (trusted contexts) ([fe572e5](https://github.com/Zach-hammad/repotoire/commit/fe572e56358c62bab25824e1ef09843ddd6243c7))
* **predictive:** resolve clippy errors and code quality issues ([dd53904](https://github.com/Zach-hammad/repotoire/commit/dd5390445c7d48478a46d2162e3032297a3e7e4d))
* propagate nesting_depth to graph nodes, refactor GodClass for DetectorContext ([ceb2614](https://github.com/Zach-hammad/repotoire/commit/ceb26145ef4463afe465a18a40b29d86fdfa5ca7))
* PrototypePollutionDetector TS — add user-input flow to fixture, flip 3 test assertions ([e7edce9](https://github.com/Zach-hammad/repotoire/commit/e7edce97257f0d2f762c6f53190724d936cae023))
* rearchitect UnreachableCodeDetector with AST-based detection (46 → 0 FPs) ([3d6e0f1](https://github.com/Zach-hammad/repotoire/commit/3d6e0f1c6d79ba2dc4985de1aa1aca8d08838199))
* reclassify 7 detectors as graph-dependent — they call graph.get_functions() ([dbdb749](https://github.com/Zach-hammad/repotoire/commit/dbdb74963bcd56242f8f869b844d71cb099d1b7d))
* reduce AI churn detector false positives on active projects ([80b7869](https://github.com/Zach-hammad/repotoire/commit/80b78694caa5ef3076304c1da4686c564b970bf3))
* reduce excessive nesting across 26 files (31 → 13 findings) ([cb29ac9](https://github.com/Zach-hammad/repotoire/commit/cb29ac92a2acfd5b83aa61d5f484cdb9dd57273e))
* reduce LazyClass false positives on Rust structs with impl blocks ([a898348](https://github.com/Zach-hammad/repotoire/commit/a89834878b4ab7b7f57b14f744650cf72f79978a))
* RegexInLoopDetector uses Python indentation-based scope, skips comments and list comprehensions ([0ffcf65](https://github.com/Zach-hammad/repotoire/commit/0ffcf6501d557d81d8fff01a6cd158be9045db0f))
* register graph-primitive detectors in FP classifier + set high confidence for GBDT bypass ([167e77b](https://github.com/Zach-hammad/repotoire/commit/167e77b3801fbe23d89597611cecebe4b3ddbde2))
* reject paths that fail canonicalize in MCP get_file (path traversal) ([90836e4](https://github.com/Zach-hammad/repotoire/commit/90836e4692867bf80d9352c4a1f6528271b28b9d))
* remove racy MAX_FINDINGS_LIMIT early-exit checks ([401fdfa](https://github.com/Zach-hammad/repotoire/commit/401fdfab249a3fe0178788fc9a7c10316e9d21b8))
* remove unused Language and LightweightParseStats re-exports ([f2779b6](https://github.com/Zach-hammad/repotoire/commit/f2779b6e6d33ce325df4bf301183536e488f0b8f))
* remove unused source parameter in collect_normalized_tokens (clippy) ([3aaf777](https://github.com/Zach-hammad/repotoire/commit/3aaf77797074df911e25b3511cba2f4aa3936965))
* rename single-character variables for clarity ([8e25b65](https://github.com/Zach-hammad/repotoire/commit/8e25b65b1f0b780f68a33a73ec15cdf9438878ef))
* replace text-based SQL detection with AST-aware method in NPlusOneDetector ([312de97](https://github.com/Zach-hammad/repotoire/commit/312de974303e4f59d3618cf7d085f1cda17d4f20))
* resolve 5 QA bugs — JSON stdout, thread panics, detector logging, gating, dead code ([d45e674](https://github.com/Zach-hammad/repotoire/commit/d45e6741cf7079b17134e25eab48a6d2ad801d5a))
* resolve 9 unwrap-without-context findings ([21011d0](https://github.com/Zach-hammad/repotoire/commit/21011d0a2821226c81f83ceb4850aadd8f2e94a4))
* resolve all clippy warnings (derivable_impls, collapsible_if, etc.) ([d5d81b3](https://github.com/Zach-hammad/repotoire/commit/d5d81b376577266f3865ef9b3394f8ecacfc8e2a))
* resolve scoring weight inconsistencies across 4 locations ([de351c6](https://github.com/Zach-hammad/repotoire/commit/de351c61d15e0f2d9d634f7ecfdf2076c0aa27cf))
* restore File node LOC computation after CompactNode migration ([386c9bd](https://github.com/Zach-hammad/repotoire/commit/386c9bda367f86f1efb38ebc256c2a7de863feb7))
* round FP classifier probability to eliminate boundary jitter ([85c7c70](https://github.com/Zach-hammad/repotoire/commit/85c7c70bdc2fd1e3e41e195fc2c6205e09b19e2a))
* route graph-wide detector findings by detector_scope, not affected_files ([adbb82f](https://github.com/Zach-hammad/repotoire/commit/adbb82f414bb5642b608231770157a62e549c5b7))
* scoring recalibration — framework detection and API surface discount ([9bbbf81](https://github.com/Zach-hammad/repotoire/commit/9bbbf81e8777e1327aed78067a5c72448102f8b5))
* SecretDetector skips variable references, settings reads, and attribute accesses in Generic Secret pattern ([83e2bfa](https://github.com/Zach-hammad/repotoire/commit/83e2bfa58bf8cc836bef6cb1b7723ea640658011))
* skip conditionally compiled code in unreachable code detector ([11237c3](https://github.com/Zach-hammad/repotoire/commit/11237c382da7bb18d813d72ec55dc8bebc5b8519))
* skip test code in regex-in-loop detector for Rust files ([e96a309](https://github.com/Zach-hammad/repotoire/commit/e96a30918e0f63b9a462cb2e8bcaefa19e924833))
* sort detector-internal HashMap iterations ([32a1eb0](https://github.com/Zach-hammad/repotoire/commit/32a1eb0a3baa6cc0213c34e9e9dcf684c68eb0b9))
* sort remaining HashMap iterations in detectors ([a106c90](https://github.com/Zach-hammad/repotoire/commit/a106c90158ebbda783ccac0752157276dd35dbda))
* SQLInjectionDetector recognizes quote_name() sanitizer and skips DB backend internals ([8d2facb](https://github.com/Zach-hammad/repotoire/commit/8d2facb7f242f34d9243dc632b3cb80c58090e57))
* StringConcatLoopDetector fixes f-string regex, adds Python indentation scope, removes = x + y pattern ([15566e9](https://github.com/Zach-hammad/repotoire/commit/15566e96c5386a3ef5bd775124ead923d0df6469))
* StringConcatLoopDetector requires 2+ concatenations to same variable per loop ([d12ff71](https://github.com/Zach-hammad/repotoire/commit/d12ff71b5097920a099761b196524d7254611701))
* tune Phase B detector thresholds from real-world validation ([f0d6bad](https://github.com/Zach-hammad/repotoire/commit/f0d6badc65bf1f9e82787f33ea9372b9abab5f23))
* UnusedImportsDetector skips function-scoped imports (indented imports inside function bodies) ([b7045ec](https://github.com/Zach-hammad/repotoire/commit/b7045ec92150bc673feb894abc4d6ee16b99a437))
* update graph-independent detector count after AIDuplicateBlock change ([0f529f2](https://github.com/Zach-hammad/repotoire/commit/0f529f2b8bf3e993125fedfa23fd339c1c836116))
* upgrade notify 7→8, indicatif 0.17→0.18 to resolve vulnerable deps ([8ac35df](https://github.com/Zach-hammad/repotoire/commit/8ac35df3a663a689e49b439769dea6eea77c2d9c))
* use BTreeMap for lookups in FlushingGraphBuilder for determinism ([be7c43f](https://github.com/Zach-hammad/repotoire/commit/be7c43f49efab40f36e76180f7983fec2004a919))
* use project config in graph-primitive detectors + add tracing span ([2a74a6e](https://github.com/Zach-hammad/repotoire/commit/2a74a6e404d1a5f8963afdbab38a412ab3185e60))
* **values:** remove redundant value_store from GdPrecomputed, add ValueStore tests ([565a981](https://github.com/Zach-hammad/repotoire/commit/565a98101841a265b821eff4d46d266b6c893437))
* WildcardImportsDetector skips all wildcard imports in __init__.py (re-export convention) ([a52edf8](https://github.com/Zach-hammad/repotoire/commit/a52edf88dbcf31731a2fdcd53eb2cc6c565dc872))
* XxeDetector recognizes custom safe parsers, skips imports and JS static data ([8dd4ab7](https://github.com/Zach-hammad/repotoire/commit/8dd4ab7d16c98f593421c12a2fcc9bad20053ef7))


### Performance Improvements

* 3 algorithm-backed optimizations — inverted index, zero-reparse, MinHash/LSH ([23ca71d](https://github.com/Zach-hammad/repotoire/commit/23ca71d129ee4c3c0d7dfbcb201c0b0dcd97bc2c))
* activate string interner in GraphStore — infrastructure for 66% node memory reduction ([0ebb815](https://github.com/Zach-hammad/repotoire/commit/0ebb815f339e2c44ddad42eeead358aa59c02bbb))
* add --timings flag for per-phase pipeline timing breakdown ([33294d8](https://github.com/Zach-hammad/repotoire/commit/33294d878696223da5e46ccf8c8099bf7226aabe))
* add CachedGraphQuery wrapper — memoizes expensive full-scan methods ([259f5a0](https://github.com/Zach-hammad/repotoire/commit/259f5a0016598f8e7b1447ea3b48438c3963d260))
* add Cargo profiling profile for perf/flamegraph symbol resolution ([f0f6b9a](https://github.com/Zach-hammad/repotoire/commit/f0f6b9a9618022c42eccb94b3a3a5c5d8e7ee53c))
* add DHAT and jemalloc feature gates for heap profiling ([aa5c96c](https://github.com/Zach-hammad/repotoire/commit/aa5c96c4bbf46232eda3fdf25c3e1a0f5670a70f))
* add is_in_import_cycle() to avoid cloning cycle list per finding ([522a115](https://github.com/Zach-hammad/repotoire/commit/522a115efa7d7fee630b112ae73a4975e46174c2))
* add metrics_cache DashMap to GraphStore for cross-phase metric reuse ([f65d647](https://github.com/Zach-hammad/repotoire/commit/f65d64794ade7f9fbafa4caa8f2f2a774d504dfa))
* add shared FileContentCache — DashMap + Arc&lt;String&gt; for zero-copy cross-detector file access ([c7a5524](https://github.com/Zach-hammad/repotoire/commit/c7a5524fd5bb372c3bed9a9178dbcce249335e16))
* add split detection API — run_graph_independent + run_graph_dependent ([f797be2](https://github.com/Zach-hammad/repotoire/commit/f797be26f542acfd383ec662aee8a3aee57b4677))
* AIDuplicateBlock pre-filter + lazy GENERIC_SET ([62fa98c](https://github.com/Zach-hammad/repotoire/commit/62fa98cce0274c60d53b89d13bd59112db58821f))
* AIMissingTestsDetector O(N*M)→O(N+M) via pre-built suffix/infix sets ([efcb8d9](https://github.com/Zach-hammad/repotoire/commit/efcb8d9f3e45e5aed7b941991b1246bbeb99ed38))
* background cache writes, compact findings JSON ([99c503c](https://github.com/Zach-hammad/repotoire/commit/99c503cd7b605e70b7dfbc265aa7df3d1c6c194a))
* batch revwalk for AIChurnDetector — 1,889ms→966ms (-49%) ([8e3b1da](https://github.com/Zach-hammad/repotoire/commit/8e3b1da40e0482aef53ea682034f063cf8b5e25f))
* bitset brute-force for AIBoilerplate — 15.7s→2.7s on CPython (-83%) ([da824e7](https://github.com/Zach-hammad/repotoire/commit/da824e75cb6ac76195f76b34dd89bb14b344eb85))
* boolean-trap raw pre-filter + lazy func_map — 2,425ms→1,646ms (-32%) ([282fbdd](https://github.com/Zach-hammad/repotoire/commit/282fbdd329cc1d6c2346477740e1600713e1809f))
* cache tree-sitter Query objects and reuse Parser via thread_local ([011a32a](https://github.com/Zach-hammad/repotoire/commit/011a32a1cdf4a97e75151caf57a6955625c02136))
* cap hidden coupling overlay edges to 1 per file pair ([204c953](https://github.com/Zach-hammad/repotoire/commit/204c95397c2168f2b555af0345b559127a9b51dc))
* centralized taint engine — single pass for all 12 security detectors ([b483551](https://github.com/Zach-hammad/repotoire/commit/b483551deaa9b53f70fec75358348b84cdb947c9))
* ClassContextBuilder file-scoped index — O(F×C)→O(C×Fc) method mapping ([bb71a08](https://github.com/Zach-hammad/repotoire/commit/bb71a08b5cfc97e7f6c3b10e41d7bf75123bc260))
* command-injection raw pre-filter — 2,219ms→391ms (-82.4%) ([9c63557](https://github.com/Zach-hammad/repotoire/commit/9c63557200adcf88c5231c3baa5825bdfccad46c))
* create Contains edges in add_nodes_batch — eliminate 168K String allocations ([2958d00](https://github.com/Zach-hammad/repotoire/commit/2958d00d0dc2b7c09ec916bae977e5c95ea19349))
* DashMap indexes for get_functions_in_file/get_classes_in_file — O(N)→O(1) ([b247117](https://github.com/Zach-hammad/repotoire/commit/b247117764626818f3516b71059f70d936fa4a5d))
* **duplicate-code:** pre-normalize lines and hash blocks for O(n) window creation ([d6fe211](https://github.com/Zach-hammad/repotoire/commit/d6fe21174d5a97ac7efeccaa6c4a6b4f29a2317b))
* **duplicate-code:** use StrKey instead of String in caller analysis ([0a402ef](https://github.com/Zach-hammad/repotoire/commit/0a402ef81c8edcfa9b5a27835a629eab253fb489))
* early detector termination when MAX_FINDINGS_LIMIT reached ([f96c597](https://github.com/Zach-hammad/repotoire/commit/f96c597cd98cab1a680f43495f862396c0d37e19))
* eliminate AIChurnDetector N+1 git queries — 154x speedup (24min → 9s) ([0e80981](https://github.com/Zach-hammad/repotoire/commit/0e80981888a584e14a8b6a9547fb5d2f59c4488d))
* eliminate O(N) get_functions() scans in mutable-default-args and empty-catch-block ([2f671fe](https://github.com/Zach-hammad/repotoire/commit/2f671fe181c7115179f3d63d33fd73961d8ffef3))
* EvalDetector raw pre-filter for eval/exec keywords — 436ms→167ms (-62%) ([cdab4ba](https://github.com/Zach-hammad/repotoire/commit/cdab4bab31bea95122c76e322196f029d2f1cf2e))
* expand parse-phase fingerprint cache for both AI detectors ([4831498](https://github.com/Zach-hammad/repotoire/commit/483149884f955fd7df17e53faafc22e916ab9cb0))
* fast diff opts across all git operations — skip_binary_check, context_lines(0), simplify_first_parent ([c8f5779](https://github.com/Zach-hammad/repotoire/commit/c8f5779c170799fc7b5f3f0457a27bfa3305bb16))
* GeneratorMisuse yield pre-filter + lazy func_map — 673ms→189ms (-72%) ([cca24d4](https://github.com/Zach-hammad/repotoire/commit/cca24d492b7b03534edc225e1f96b8d66960e5f8))
* GraphQuery trait refactor, detector migrations, parser nesting depth enrichment ([dfda259](https://github.com/Zach-hammad/repotoire/commit/dfda2591abad3bc9fa921247201283a4d002d486))
* InfiniteLoop pre-filter + lazy func_map — 2,410ms→1,027ms (-57.4%) ([056243a](https://github.com/Zach-hammad/repotoire/commit/056243a725583c808cd4302241bb7d594e98abaa))
* insecure-crypto raw pre-filter — 713ms→72ms on CPython ([99307bd](https://github.com/Zach-hammad/repotoire/commit/99307bd9e835e0f5aa7f71402fe65e03b3f16a48))
* insecure-random pre-filter + lazy func_map — 2,679ms→414ms (-84.6%) ([78b3b12](https://github.com/Zach-hammad/repotoire/commit/78b3b12f200ebaae97ec3252fa6c9af8264547ee))
* LazyClass file-scoped index + cleanup — 492ms→167ms (-66%) ([e4e95d1](https://github.com/Zach-hammad/repotoire/commit/e4e95d188ca64fd80fb123285b80958adb3b71e0))
* magic-numbers raw content + hardcoded-timeout/insecure-cookie pre-filters ([1670398](https://github.com/Zach-hammad/repotoire/commit/1670398cdc9e7a28a334d881195dfbdea32fb02c))
* **magic-numbers:** early exit for files/lines without digit sequences ([62094c7](https://github.com/Zach-hammad/repotoire/commit/62094c71bbe976dd1465c3784f41af0fa8cb45ed))
* make jemalloc the default allocator — 29% faster wall clock ([be72ce8](https://github.com/Zach-hammad/repotoire/commit/be72ce86012a6e5d512df1ccfdf926251f194ef8))
* MessageChain pre-built qn→file map — 1,116ms→324ms (-71%) ([52fa49e](https://github.com/Zach-hammad/repotoire/commit/52fa49ec797675602ee0710510fed98367416aee))
* MiddleMan file-scoped index — 1,732ms→181ms (-89.5%) ([f442e49](https://github.com/Zach-hammad/repotoire/commit/f442e4951aede5718948ac5d9f00c93ed1d8621a))
* migrate ShotgunSurgery, RegexInLoop, UnreachableCode to DetectorContext ([f60f9c3](https://github.com/Zach-hammad/repotoire/commit/f60f9c30de155d94acd6bec7f028b925432a0db3))
* MinHash Jaccard estimation, lazy engine startup, detector pre-filters ([5d1b932](https://github.com/Zach-hammad/repotoire/commit/5d1b93282d38eb0422ba978a15d6bb0e96e7d51e))
* O(n²)→O(n) test context scan + SQL injection regex to static LazyLock ([dd7802d](https://github.com/Zach-hammad/repotoire/commit/dd7802d5c070ee29c304d368ffefdf406cf7f377))
* optimize cross-function taint BFS — 85s→5s on CPython (17x faster) ([fd3326d](https://github.com/Zach-hammad/repotoire/commit/fd3326d673284b06ed1de9bb657eef8520a1526a))
* optimize GI detectors — wildcard-imports, hardcoded-ips, cors-misconfig ([66a78d3](https://github.com/Zach-hammad/repotoire/commit/66a78d3a201bd4b452267354a0f1116001b01e13))
* parallel file walking via ignore::WalkParallel in collect_source_files ([48fcf91](https://github.com/Zach-hammad/repotoire/commit/48fcf916623bf7184ff772ace0e39d085075b0f1))
* parallelize AIChurnDetector file revwalks with rayon — 9.9s → 4.2s ([f76ea59](https://github.com/Zach-hammad/repotoire/commit/f76ea59d53185fb166bbee22d82d8dd8728d659a))
* parallelize HMM context filter with rayon — 2.1s → 0.3s ([e400c2b](https://github.com/Zach-hammad/repotoire/commit/e400c2b0d631443f3795a0a23fc9d2ee7bfeabb0))
* pre-allocate HashMaps and merge node batch inserts ([27479f5](https://github.com/Zach-hammad/repotoire/commit/27479f59d40190a6ee105fdfbc5d4a4d00ced4d4))
* pre-allocate petgraph node/edge capacity from parse result counts ([88674c9](https://github.com/Zach-hammad/repotoire/commit/88674c9d6db5fe1c2e1e7ddd51cfadb73a309fbd))
* pre-build caller/callee maps in UnreachableCodeDetector — 1,115ms→476ms (-57%) ([fa1feb8](https://github.com/Zach-hammad/repotoire/commit/fa1feb883d90275f4be247d7787b8ba88ed04e78))
* pre-build name→CodeNode map in boolean-trap — eliminate O(N) per finding ([91d06b9](https://github.com/Zach-hammad/repotoire/commit/91d06b9621b37c17bdf314037603b3530228cbfe))
* pre-compute content flags during DetectorContext build for path_traversal pre-filter ([79674b5](https://github.com/Zach-hammad/repotoire/commit/79674b5f52803c64821463976b2df77ee8f4ed56))
* pre-compute fan-in for HMM sort — eliminate O(n log n) graph queries ([f052b6d](https://github.com/Zach-hammad/repotoire/commit/f052b6df7cd3ed72083dabd93a96adf223919346))
* pre-filter files by sink keywords — skip 90%+ of irrelevant files before taint analysis ([55fb019](https://github.com/Zach-hammad/repotoire/commit/55fb0192b75ea3d275af302879f4afa42ee8bff6))
* pre-lowercase sinks + early return in check_sink_call — eliminate per-line allocations ([7b7a630](https://github.com/Zach-hammad/repotoire/commit/7b7a6306162e58d8cc6e4a59834810c8d1b2f54c))
* pre-size function_lookup, pending_calls, deferred_imports containers ([9ae6c35](https://github.com/Zach-hammad/repotoire/commit/9ae6c35733e00a02ff4cae168dfe73e80edb84ca))
* pre-warm masked cache + tighter boolean-trap pre-filter ([704aa16](https://github.com/Zach-hammad/repotoire/commit/704aa16fee35c88f0dc09b263c1da57db2a6741f))
* **predictive:** replace O(n²) L3 with Mahalanobis distance, fix CPython regression ([d2afd47](https://github.com/Zach-hammad/repotoire/commit/d2afd479fec5f429405f10629f68e703c2f5da93))
* regex compilation audit — migrate 130 patterns from OnceLock to static LazyLock ([d627ad6](https://github.com/Zach-hammad/repotoire/commit/d627ad6a8004e2b94f6ef28e99a5a7185c204cce))
* replace cache Mutex with DashMap for lock-free parallel parsing ([a0436e8](https://github.com/Zach-hammad/repotoire/commit/a0436e8dfbaaf85fb9de7b6b16e01b0a3a41d1e4))
* replace node_index RwLock&lt;HashMap&gt; with DashMap — lock-free concurrent reads ([a531fb2](https://github.com/Zach-hammad/repotoire/commit/a531fb2715b34e76d790432e4136cf754e93af6a))
* replace SipHash with XXH3 for incremental cache content hashing ([4aa19ef](https://github.com/Zach-hammad/repotoire/commit/4aa19ef2fc2034aedcfdbac044f22ca7fc76e68c))
* sampled Brandes betweenness — K=500 source nodes for O(K*E) instead of O(N*E) ([fee31c8](https://github.com/Zach-hammad/repotoire/commit/fee31c81b2ee37e8ac5972f43ebaba25145920ce))
* secret-detection raw pre-filter — 3,803ms→1,420ms (-62.7%) ([50bf5b4](https://github.com/Zach-hammad/repotoire/commit/50bf5b4dc5c507269fe2eb4b0033dce564f355c8))
* ShotgunSurgery file-scoped index — 2,887ms→791ms (-72.6%) ([2433a98](https://github.com/Zach-hammad/repotoire/commit/2433a98f3859b332284ea8e430a810cbcc0544ff))
* single-pass churn+hunk extraction for AIChurnDetector — accuracy improvement ([cae59a5](https://github.com/Zach-hammad/repotoire/commit/cae59a5a493ff9d68d682607e2d558c8629732a1))
* skip function context building in graph-independent detection phase ([4949da3](https://github.com/Zach-hammad/repotoire/commit/4949da38320a166b05e43a41847507023669d565))
* speculative detection — file-local detectors run in parallel with git enrichment ([07256b9](https://github.com/Zach-hammad/repotoire/commit/07256b999cd315aa4ff4551b0538285e87cb8e2d))
* split parse_file into parse_file/parse_file_with_values ([2ac7ccb](https://github.com/Zach-hammad/repotoire/commit/2ac7ccbbc1a0c42e01bc67055b75852e96f1d01e))
* tag 42 detectors as graph-independent for speculative execution ([ef92219](https://github.com/Zach-hammad/repotoire/commit/ef922190c416f491bc6cf8454f00be0cbcbe834e))
* two-phase edge resolution in FlushingGraphBuilder for deterministic graphs ([425275d](https://github.com/Zach-hammad/repotoire/commit/425275d03f69f62e44c59f5f92023416d33fdc0f))
* unify detection pipeline — remove streaming path, all repos use speculative parallelism ([0b35a33](https://github.com/Zach-hammad/repotoire/commit/0b35a33690bde38a6a2871c9fca3abf2cc281b27))
* UnusedImportsDetector O(K×N)→O(N+K) via pre-built word set ([6380c1c](https://github.com/Zach-hammad/repotoire/commit/6380c1c98a71ecfd54bf8fad454e5f454a22f79f))
* use indexed lookups in test-in-production, large-files, commented-code ([1d78bd5](https://github.com/Zach-hammad/repotoire/commit/1d78bd59b374b7926fbbd0a9da0af8a94ecfa783))
* walk+parse overlap — stream file paths to parsers as discovered ([d672da6](https://github.com/Zach-hammad/repotoire/commit/d672da6355bed7f5fce35967226fad27a7170966))
* wire CachedGraphQuery into DetectorEngine — all detectors use cached graph ([0090682](https://github.com/Zach-hammad/repotoire/commit/009068224a246d9bd743f98bad3f3d645367b075))
* wrap ParseResult in Arc to avoid deep clones on cache hits ([9832b76](https://github.com/Zach-hammad/repotoire/commit/9832b76d0cfb303963825152e5ff7ad43c8a70fd))

## [Unreleased]

### Bug Fixes

- CI tests - add Rust toolchain and env vars
- Safe Clerk hooks for build-time prerendering
- Align Stripe env vars with Fly.io secrets
- Align all pricing copy to Team plan naming
- Sync version to 0.1.35 in bumpversion config and __init__.py

### Features

- Standardize pricing to $15/dev/mo (annual) / $19/dev/mo (monthly)

### Performance

- Optimize graph stats queries with UNION pattern
- Lazy imports for CLI cold start optimization
- Lazy imports for monorepo CLI module

### Testing

- Add tests for kuzu client, factory, and billing
- Add tests for CLI sync API routes
- Add tests for team analytics API routes

## [0.1.35] - 2026-02-06

### Bug Fixes

- E2E testing bug fixes
- Lint issues in billing, team_analytics routes
- Cli_sync.py field mapping to match DB models
- Cli_sync.py Repository model field compatibility
- Billing checkout metadata keys to match webhook handler
- Use asyncio.run() instead of deprecated get_event_loop pattern
- GitHub Action use --changed flag instead of non-existent --files
- Clerk build warnings, TypeScript errors, lint fixes
- Skip PageRank benchmark test when scipy not installed
- Implement missing metrics in engine.py
- Npm vulnerabilities + more query optimizations

### Documentation

- Update for local-first Kuzu CLI

### Features

- Complete call resolution for Kuzu local mode
- Add team analytics (cloud-only)
- **web:** Split landing page into CLI + Teams
- **api:** Complete team analytics backend implementation
- **cli:** Add 'repotoire sync' to upload local analysis to cloud
- **billing:** Add Stripe checkout and portal endpoints
- Stripe checkout, GitHub PR checks, async team analytics
- Wire up email notifications for changelog and status updates

### Miscellaneous

- Bump version to 0.1.34, add CHANGELOG and .env.example
- Fix ruff config deprecation, add pre-commit hooks

### Performance

- Lazy imports for faster cold starts
- Optimize graph queries with UNION instead of label scan

## [0.1.31] - 2026-02-05

### Bug Fixes

- CLI fix command and BYOK mode bugs (v0.1.10)
- Findings table now shows file paths and line numbers (v0.1.12)
- /write endpoint returns full query results
- Make enricher resilient to different FalkorDB response formats
- Rewrite NOT EXISTS query for FalkorDB compatibility
- Make 'Why It Matters' specific to each finding, no fallbacks
- Fix file:line mode in fix command (use affected_files not file_path)
- Kuzu Cypher compatibility for more detectors
- Enable 29/42 detectors in Kuzu mode
- Kuzu Cypher compatibility - 30/42 detectors working
- Kuzu Cypher compatibility improvements
- Kuzu compatibility - 33/42 detectors (79%)
- Enable TypeHintCoverage and PackageStability for Kuzu (83% coverage)
- Enable 90% of detectors for Kuzu local mode
- Enable 98% of detectors for Kuzu (41/42)
- Enable 100% of detectors for Kuzu (42/42)
- Kuzu schema and client compatibility
- Kuzu compatibility for graph detectors
- Kuzu compatibility - skip unsupported operations

### Documentation

- Add local mode quick start to README

### Features

- Add --top and --severity filters to analyze command (v0.1.11)
- Add /write endpoint for detector metadata operations (v0.1.13)
- Add 'Why It Matters' explanations to findings (REPO-521)
- Add fuzzy matching for fix application to handle line drift
- Add --changed flag for incremental analysis (REPO-523)
- Cache path cache data locally to skip API queries (REPO-524)
- Add Kùzu embedded graph database client (REPO-525)
- Add create_kuzu_client and create_local_client to factory
- Kuzu embedded graph DB for local-first CLI
- Disable graph detectors in Kuzu mode for clean local analysis
- Rust-accelerated fix applicator (REPO-525)
- Fix path cache for Kuzu with UNION queries
- External* entity tracking for Kuzu local mode
- Enable call resolution + CALLS relationship properties for Kuzu
- Enable embeddings by default on ingest
- Improved error UX with friendly messages

### Miscellaneous

- Bump to 0.1.17 - 33/42 detectors (79%)

### Performance

- Add node data prefetch cache to reduce cloud HTTP round-trips (REPO-522)

### Ux

- Improve error messages for local mode

### Wip

- Degree centrality partial fix for read-only mode
- Call resolution infrastructure for Kuzu

## [0.1.9] - 2026-02-05

### Features

- Add 'findings' command to view/browse findings in CLI (v0.1.9)

## [0.1.8] - 2026-02-05

### Features

- Add simple 'fix' command for tight analyze→fix loop (v0.1.8)

## [0.1.7] - 2026-02-05

### Bug Fixes

- **lean:** Use List.all_eq_true instead of String.all_iff
- Cloud API compatibility issues (v0.1.7)

### CI/CD

- Re-enable Lean workflow after fixing String.all proof

## [0.1.5] - 2026-02-05

### Documentation

- Update changelog

## [0.1.6] - 2026-02-05

### Bug Fixes

- **cli:** Lazy-load database + use X-API-Key header for graph API
- **api:** Support API key auth for /usage endpoint + fix CLI headers
- Move require_org_api_key after get_current_user_or_api_key definition
- Remove FK constraint on audit_logs.organization_id
- Correct BugPredictor.load() call signature and predict() usage
- Train-bug-predictor accepts multi-project training data format
- Update Lean proof to work with newer String.all definition
- Alternative proof for string_all_imp lemma
- Use stable Lean 4.14.0 instead of RC version
- Restore original proof with stable Lean version

### CI/CD

- Disable Lean workflow temporarily

### Documentation

- Highlight graph-powered detectors in CLI and README

### Features

- **auth:** Add require_org_api_key dependency for API key support
- Integrate InsightsEngine for ML enrichment and graph metrics (REPO-501)
- Add heuristic risk scoring as ML fallback
- Add universal model training script + improve risk display
- Support local FalkorDB fallback when no API key

### Miscellaneous

- Cache bust
- Remove marketplace feature

### Performance

- Move heavy ML deps to optional [local-embeddings]

## [0.1.4] - 2026-02-04

### Bug Fixes

- Handle uv-secure JSON line wrapping in dependency scanner
- **historical:** Allow git extraction from subdirectories
- **api:** Correct historical router prefix
- **historical:** Use existing Neo4j connection for Graphiti
- **historical:** Use FalkorDriver for Graphiti connection
- **security:** Address critical vulnerabilities and code quality issues
- **launch:** Address remaining launch readiness issues
- **tests:** Add tests __init__.py for proper package imports
- Update homepage trial text from 14 to 7 days
- Add /features and /how-it-works to public routes
- **ingestion:** Revert slow size(split()) patterns to native CONTAINS
- **ingestion:** Remove C++/Rust '::' filter from entities query
- **ingestion:** Include class methods in community mapping + remove redundant :: filters
- **graph:** KG-3 - connect internal CONTAINS relationships properly
- **logging:** Remove duplicate log statements from falkordb_client
- **api:** Add embedding backend env var support and tenant isolation
- Sync version to 0.1.3 in __init__.py
- **graph:** Address 7 issues from security/performance audit
- **web:** Remove mock data and legacy routes causing multi-tenant data leak
- **api:** Implement actual Graphiti queries for git history endpoints
- **ci:** Add missing files for CI to pass
- **billing:** Improve security, validation, and testability
- **ci:** Remove timestamp from generated types header
- Remove shadowing Path import and update neo4j->falkordb test
- **tests:** Update tests for async fixtures and API changes
- **api:** Enable preview_fix endpoint to work with database fixes
- **tests:** Convert asset storage tests to async/await
- **tests:** Update tests for refactored detectors and cloud client
- **api:** Correctly await get_preview_cache in dependency
- **tests:** Update test assertions for refactored implementations
- **tests:** Fix GraphSAGE skip check and API key validation mock
- **sandbox:** Ensure directories exist before creating __init__.py
- **tests:** Update integration tests for refactored APIs
- **sandbox:** Preserve absolute paths when creating __init__.py files
- **tests:** Fix FalkorDB integration test connection issues
- **web:** Handle missing Clerk key during CI builds
- **web:** Use placeholder Clerk key for CI builds
- **ci:** Add Clerk publishable key fallback in workflow
- **security:** Harden Stripe integration with multiple security improvements
- **security:** Address critical and high severity bugs across backend
- Address critical bugs identified in E2E workflow analysis
- **api:** Add stub billing endpoints for Clerk migration
- Address security, UX, and reliability issues from comprehensive audit
- **api:** Return null for payment-method stub endpoint
- **web:** Use Clerk's openOrganizationProfile for billing
- Comprehensive bug fixes from second audit round
- Address critical audit findings for production readiness
- Comprehensive launch readiness improvements
- Resolve SQLAlchemy index and FastAPI dependency issues
- Resolve 5 failing test cases
- **db:** Add in_app_notifications table and fix conditional indexes
- **web:** Make service worker resilient to missing files
- **web:** Remove missing manifest.json and add Clerk redirect props
- **security:** Prevent SQL injection in TimescaleDB INTERVAL clause
- **a11y:** Add SheetTitle and aria-describedby to mobile navigation
- **scoring:** Factor findings into health score calculation
- **alembic:** Handle asyncpg URLs and sslmode for Neon migrations
- **models:** Remove default value from issues_score for dataclass ordering
- **db:** Add CANCELLED status to AnalysisStatus enum
- **workers:** Remove duplicate _get_graph_client_for_org function
- **detectors:** Rename metadata to graph_context in MiddleManDetector
- **workers:** Increase task time limits for large analyses
- **workers:** Reset Redis concurrency counters on cleanup
- **workers:** Update repository health_score after analysis completes
- **api:** Filter Repository lookup by organization_id
- Add batch_get_file_metadata to CloudProxyClient
- Add batch_delete_file_entities to CloudProxyClient
- **api:** Use clerk_org_id instead of org_slug for organization lookups
- **detectors:** FalkorDB IN operator type mismatch in DeadCodeDetector
- **detectors:** Use list comprehension for FalkorDB IN operator
- **detectors:** Use NOT any() for FalkorDB list membership check
- **detectors:** Remove hardcoded path filters from DeadCodeDetector
- Remove repo-specific hardcoding for universal compatibility
- Use performance cpu_kind for fly.io worker
- Route cloud clients through dedicated NLQ API endpoints
- Pass user context to NLQ API handlers for tenant isolation
- Worker permission error and DeadCodeDetector type mismatch
- **detectors:** Handle empty exports list in FalkorDB any() check
- **detectors:** Remove problematic exports check from FalkorDB queries
- **detectors:** Remove Neo4j branches from DeadCodeDetector for FalkorDB-only usage
- **detectors:** Remove label filter from GodClassDetector LCOM query
- **detectors:** Move GodClassDetector pattern checks to Python
- **worker:** Optimize Celery memory configuration for ML workloads
- **dedup:** Unified grouping module to prevent duplicate findings
- **detectors:** Resolve FalkorDB size() type mismatch in DeadCodeDetector
- **detectors:** Move decorator check from Cypher to Python in DeadCodeDetector
- **detectors:** Move is_method filter from Cypher to Python
- **detectors:** Replace NOT patterns with OPTIONAL MATCH + IS NULL
- **detectors:** Use count() = 0 instead of IS NULL for FalkorDB compatibility
- **detectors:** Simplify dead class query - remove STARTS WITH clause
- **detectors:** Fix JscpdDetector for multi-language support
- **api:** Add API key auth to analysis and analytics routes
- **detectors:** Add missing json import and remove duplicate metadata
- Add response_model=None to report download endpoint
- Use create_client instead of non-existent get_factory
- Allow JWT auth for graph routes (frontend compatibility)
- Update historical.py to use renamed GraphUser
- **api:** Resolve OOM by caching CodeEmbedder singleton
- **detectors:** Use direct CONTAINS relationship in god_class queries
- **deploy:** Pin FalkorDB v4.14.10 and fix thread oversubscription
- **stability:** Comprehensive REPO-500 stability improvements
- **security:** Add Cypher query validation and complete REPO-500 reliability fixes
- **api:** Add error handling for GitHub API calls in github routes
- **sync:** Comprehensive thread-safety fixes across codebase
- **sync:** Add thread-safety locks across 15 modules
- **sync:** Add thread-safety locks across 15 modules
- **billing:** Restore missing billing module and fix import errors
- **tests:** Exclude sample repos from test collection
- **parsers:** Extract type_parameters from TypeScript function declarations
- **tests:** Sync tests with REPO-500 graph naming and env isolation
- GitHub webhook CSRF exemption and improved JSON error logging
- Worker memory optimization + profiling
- Graph endpoint org lookup fallback to internal UUID
- Remove invalid TCP health check from worker
- Eagerly load subscription in get_org_by_clerk_id to avoid async lazy load error
- Pass correct repo_id to analyze_repository in analyze_repo_by_id
- Use correct node alias for isolation filter in edge queries
- Use f1 isolation filter for call edge queries in CallChainDepthDetector
- Link new orgs to Clerk org_id for API key validation
- Set graph_database_name in Clerk webhook org creation
- Replace structlog .bind() with standard logging in fix generation
- Add anthropic extra to Dockerfile for AI fix generation
- Configure LanceDB vector store for AutoFixEngine retriever
- Add finding_ids to GenerateFixesRequest types (manual sync)
- Eagerly load org.subscription to prevent async lazy load error
- Align embedding backends and disable compression for dimension consistency
- Instruct LLM to generate complete code, not just signatures
- Fuzzy code matching + improved LLM prompt for autofix
- Correct alembic revision ID
- **api:** Accept Clerk org ID in BYOK api-keys endpoints
- Exclude web/ from Docker context to speed up builds
- **api:** Lookup DB user from Clerk ID in BYOK api-keys endpoints
- **api:** Accept Clerk org ID in detector settings and rules endpoints
- **web:** Use Clerk org ID instead of slug for API calls
- **db:** Add missing in_app_notifications column to email_preferences

### Build

- **docker:** Switch from Node.js to Bun for faster JS tooling

### CI/CD

- Remove integration-neo4j job from test workflow
- Add Rust toolchain and maturin for FalkorDB tests

### Documentation

- Update CLAUDE.md to reference FalkorDB instead of Neo4j
- Add architecture-patterns.json to prevent /improve false positives
- Add Multi-Tenant Architecture section to CLAUDE.md
- Add security patterns to architecture-patterns.json
- Add API patterns to architecture-patterns.json
- Add frontend billing patterns to architecture-patterns.json
- Update architecture-patterns with webhook security details
- Update changelog

### Features

- Server-side embeddings + cloud mode CLI fixes
- **historical:** Migrate from Neo4j to FalkorDB for Graphiti
- Integrate git history into ingest flow automatically
- Add graphiti-core to core dependencies
- **provenance:** Add provenance settings UI and historical API tests
- **billing:** Switch to 7-day free trial model with Clerk Billing
- **embeddings:** Auto-generate embeddings for Pro/Enterprise plans
- **embeddings:** Use DeepInfra Qwen3-Embedding-8B instead of OpenAI
- **web:** Dashboard UX improvements and standardization
- **web:** Findings page improvements with detail view and code viewer
- **findings:** Add status workflow management
- **dashboard:** Comprehensive UX and accessibility improvements
- **repos:** Improve UX with smart routing and actionable overview
- **fixes:** Enhance UX with friendly descriptions, tooltips, and confirmation dialogs
- **fixes:** Add data consistency sync between findings and fixes
- **web:** Add openapi-typescript for automated type generation
- **docker:** Add sandbox extras for E2B support
- **docker:** Add sandbox extras to Dockerfile.api for E2B support
- **web:** Add comprehensive billing UI components
- **web:** Integrate billing components into dashboard billing page
- **stripe:** Complete Stripe integration with deduplication and Connect support
- **web:** Enhance UI with animations, dashboard components, and marketing updates
- Comprehensive UX/DX improvements across web and CLI
- Add security hardening for SaaS launch
- **workers:** Add automatic cleanup for stuck analyses
- **workers:** Add automatic git history ingestion during analysis
- Wire Rust speedups to frontend and CLI
- **detectors:** Add incremental analysis and repo_id filtering
- Replace Graphiti with GitHistoryRAG (99% cost reduction)
- **parsers:** Add TypeScript and JavaScript parser support
- **models:** Add language field to Finding for multi-language support
- **detectors:** Add ESLint hybrid detector for TypeScript/JavaScript
- **detectors:** Add TypeScript detectors with Bun runtime support
- **mcp:** Add analysis trigger tools to MCP server (REPO-432)
- **github:** Add GitHub Checks API integration for PR analysis
- **parsers:** Complete TypeScript/JavaScript parser with 4 key improvements
- **parsers:** Add Java parser with tree-sitter
- **parsers:** Add Go parser with tree-sitter
- **settings:** Add detector threshold configuration (REPO-430)
- **rules:** Add custom rules management UI (REPO-431)
- **analysis:** Add report download API endpoint (REPO-433)
- **security:** Add secrets scanning UI and API (REPO-434)
- **monorepo:** Add monorepo support to web UI (REPO-435)
- **graph:** Add Cypher query playground for power users (REPO-436)
- **settings:** Add pre-commit hook configuration wizard (REPO-437)
- **rust:** Add batch performance optimizations (REPO-403 to REPO-408)
- **rust:** Wire Rust performance functions into Python codebase
- **rust:** Add SIMD-optimized similarity and parallel string operations
- Wire SIMD-optimized Rust functions into Python codebase
- **rust:** Add transitive closure cache for O(1) reachability queries
- Integrate path expression cache into detectors (REPO-416)
- **embeddings:** Add automatic memory optimizations for vector storage
- **reporting:** Add new report formats and customization (Phases 1-2)
- Comprehensive codebase improvements (Phases 1-8)
- **config:** Consolidate configuration system with RepotoireConfig as single source of truth
- **tenant:** Add async-safe TenantContext and middleware for multi-tenant isolation
- **tenant:** Add automatic tenant resolution and tenant-aware logging
- **tenant:** Add node-level tenant filtering for defense-in-depth isolation
- **tenant:** Complete multi-tenant isolation + remove deprecated Graphiti
- **api:** Add idempotency middleware, cursor pagination, and async fixes
- **billing:** Enforce subscription limits across frontend and backend
- **parsers:** Add modern language feature support and generic fallback parser
- **taint:** Add backward taint analysis (find_taint_sources)
- Add LanceDB persistent storage support
- Add profiling to analysis tasks
- Add finding_ids parameter for selective fix generation
- Add 'Generate AI Fixes' button to findings page
- Add retry mechanism with error feedback for fix generation
- BYOK API keys for AI fixes
- Add clear error when no API key configured for AI fixes
- **frontend:** Add BYOK AI provider keys settings page

### Fix

- Add httpx[http2] for HTTP/2 support

### Miscellaneous

- Add npm lock files
- Regenerate API types
- Add .claude and .playwright-mcp to gitignore
- **infra:** Increase worker memory to 8GB for embedding model
- **detectors:** Add debug logging to DeadCodeDetector with version marker
- **detectors:** Remove debug logging from DeadCodeDetector
- Update uv.lock
- **deploy:** Improve deployment flow with tests, security, and optimization
- **fly:** Optimize for minimal cost when idle
- Upgrade FalkorDB to v4.16.1

### Performance

- **api:** Parallel LLM calls + MCP server timeouts
- Add Phase 1 performance optimizations for embeddings and git
- Add Phase 2 tree-sitter parallel parsing (13x speedup)
- **detectors:** Fix FalkorDB integration and optimize with Rust batch hashing
- **detectors:** Leverage Rust for 10x+ speedups across detectors
- Implement path_cache for dead code detectors and dynamic workers
- Reduce FalkorDB pressure during ingestion
- Further reduce FalkorDB pressure
- Ultra-conservative FalkorDB settings (batch 10, 500ms delays)
- Increase batch size to 100, reduce delays to 50ms (BGSAVE disabled)

### Refactoring

- **cli:** Remove legacy Neo4j options from cloud-only CLI
- **cli:** Remove Neo4j options from cloud-first commands
- **cli:** Convert monorepo and ml commands to cloud client
- **cli:** Remove historical commands for server-side integration
- Replace Neo4jClient references with FalkorDBClient
- Remove neo4j_graphrag dependency, use OpenAI SDK directly
- **web:** Rename middleware.ts to proxy.ts for Next.js 16
- **voting:** Migrate to unified grouping module
- Remove embeddings and vector store
- Store embeddings in LanceDB only (not on graph nodes)

### Testing

- Update historical routes tests for new backfill behavior
- **detectors:** Add comprehensive health delta calculator tests
- **detectors:** Add integration tests for DeadCodeDetector decorator queries
- **parsers:** Add comprehensive tests for TypeScript parser features
- Add unit tests for encryption utility

### Cleanup

- Remove debug print statements, keep logger calls

### Debug

- Add diagnostic logging to path_cache building
- Add print() statements to diagnose path_cache import
- Log validation errors for fix generation

### Revert

- **web:** Restore purple color scheme to match logo
- **web:** Restore Inter, Space Grotesk, and Geist Mono fonts

### Ux

- Better error messages for org mismatch and missing org context

## [0.1.3] - 2025-12-29

### Features

- **cli:** Implement cloud-only architecture with API graph proxy
- Cloud-only CLI with FalkorDB backend fixes

## [0.1.2] - 2025-12-29

### Bug Fixes

- **build:** Include LICENSE file in source distribution

## [0.1.1] - 2025-12-29

### Bug Fixes

- Resolve critical bugs in parser and graph relationship handling
- Security hardening, batch relationships, and inheritance extraction
- Add findings support and improve data ingestion robustness
- Correct Neo4j config serialization and inheritance relationships
- Improve Python parser relationship extraction
- Improve dead code detection and call resolution
- Extract nested functions and track function references (USES)
- Update Neo4j configuration to use port 7688
- Handle both absolute and relative file paths from mypy
- Resolve GodClassDetector and integration test failures
- Use embed_query instead of non-existent embed_documents
- MCP server generates correct import paths even with path mismatch
- MCP server properly handles dependency injection parameters
- Correct TemporalIngestionPipeline constructor parameter name
- Improve DI parameter detection in MCP schema generation
- **tests:** Update GodClassDetector tests for REPO-152 community analysis
- **rust:** Add assert statement handling to W0212 protected-access
- **rust:** Exclude exception classes from R0903 check
- **rust:** Exclude enums and dataclasses from R0903 check
- **rust:** Improve R0902 and W0201 for dataclass handling
- **graph:** Replace APOC with pure Cypher for FalkorDB compatibility
- **web:** Add missing Slider component and Suspense boundaries
- **web:** Wait for auth before making analytics API calls
- **web:** Fix TypeScript type assertion in cookie-consent
- **web:** Fix remaining TypeScript type assertions in cookie-consent
- **web:** Wrap useSearchParams in Suspense boundary
- **db:** Use lowercase enum values for analysis_status
- **worker:** Fix Neo4j SSL and Celery module path
- **worker:** Fix AnalysisEngine import path and logger calls
- **graph:** Use MERGE instead of CREATE for idempotent re-ingestion
- **worker:** Fix enum case and AnalysisEngine signature
- **worker:** Prevent SSL timeout by using short-lived DB sessions
- **db:** Use MemberRole enum .value for PostgreSQL comparisons
- **worker:** Use extra={} for logger keyword arguments
- **db:** Fix Alembic migration enum handling for PostgreSQL
- **api:** Deduplicate findings by using latest analysis run only
- **docs:** Add /docs to public routes in middleware
- Dashboard analytics and repository linking improvements
- Correct router prefix for code routes
- Handle None claims in API key verification
- Run sync Clerk SDK call in thread to avoid blocking event loop
- Use sync database for API billing check to avoid greenlet issues
- Use environment variables for Neo4j connection in code routes
- Use get_sync_session context manager for database access
- Use async session for billing check instead of sync psycopg2
- Eagerly load subscription relationship to avoid lazy load error
- Security and reliability improvements across codebase
- **tests:** Add autouse cleanup fixtures for test isolation
- **rust:** Improve error handling and optimize duplicate detection
- **detectors:** Add timeout parameter to subprocess.run() calls
- **graph:** Add empty result check in Neo4j client create_node
- **api:** Add LRU eviction policy to in-memory caches
- Resolve OpenAPI KeyError and e2e test failures
- **ci:** Consolidate release workflow for single package
- **ci:** Use uv tool install instead of pip install --system
- **ci:** Skip builds in dry run mode, update macOS runner
- **ci:** Fix maturin cross-compilation for Linux targets
- **ci:** Install OpenSSL for Linux builds, remove retired macOS Intel
- **ci:** Disable Linux ARM64 cross-compilation for now
- **ci:** Use native ARM runner and macos-15-intel for full platform coverage
- **ci:** Fix publish conditions for tag push events
- **ci:** Ensure publish jobs run for tag push events

### CI/CD

- Add test workflow with differential tests
- **sandbox:** Add GitHub Actions workflow for sandbox tests

### Documentation

- Add comprehensive configuration documentation (FAL-59)
- Create comprehensive user guide with examples (FAL-66)
- Expand architecture documentation with design decisions (FAL-65)
- Enhance models.py with comprehensive docstrings and examples (FAL-64)
- Create comprehensive Jupyter notebook examples (FAL-67)
- Update CLAUDE.md with connection pooling and env examples
- Add comprehensive guides and test results
- Add architecture and security documentation
- Add comprehensive hybrid detector pattern documentation
- Document hybrid detector architecture and fix parser bug
- Add comprehensive RAG API documentation and CLAUDE.md integration
- **REPO-140:** Add comprehensive TimescaleDB documentation (Phase 7)
- Add CI/CD auto-fix integration and detector audit guides
- **sandbox:** Add sandbox documentation and examples
- Add documentation site with API, CLI, and webhooks docs
- Add query cookbook and prompt documentation
- Remove stale internal documentation
- Add reorganized documentation structure
- Update changelog

### Features

- Add module nodes, override detection, and composite indexes
- Implement three production-grade code smell detectors
- Implement Phase 6 infrastructure (logging, config, security)
- Implement environment variable fallback chains (FAL-57)
- Add configurable batch sizes and detector thresholds (FAL-58)
- Add Neo4j connection retry logic with exponential backoff (FAL-53)
- Add CLI input validation with helpful error messages (FAL-54)
- Add configuration validation on startup (FAL-55)
- Add falkor validate command (FAL-63)
- Add progress bars for ingestion and analysis (FAL-61)
- Enhance CLI output formatting with Rich (FAL-60)
- Add HTML report export with code snippets (FAL-62)
- Implement secrets detection during code ingestion (FAL-100)
- Complete INHERITS relationship extraction (FAL-84)
- Implement incremental ingestion with file hash tracking (FAL-93)
- Add advanced connection pooling to Neo4j client
- Enhance entity models with AI and temporal support
- Enhance graph queries and schema for temporal analysis
- Improve detectors with new algorithms and exports
- Add migration commands and AI clue generation
- Add advanced code smell detectors
- Add tree-sitter parser framework for multi-language support
- Add database migrations and temporal tracking
- Add spaCy-powered AI clue generator
- Add git integration for temporal analysis
- Complete detector testing and tuning (REPO-116)
- Implement RuffImportDetector to replace TrulyUnusedImportsDetector
- Add MypyDetector hybrid detector for type checking
- Add Pylint, Bandit, and Radon hybrid detectors
- Optimize hybrid detectors for 6x performance improvement
- Add Vulture and Semgrep hybrid detectors
- Implement DECORATES relationship tracking for decorators
- Add RAG Phase 1 - Embedding Infrastructure
- Add RAG Phase 2 - Hybrid Retrieval System
- Add RAG Phase 3 - FastAPI application for code Q&A
- Integrate embedding generation into ingestion pipeline
- Add comprehensive RAG integration tests with OpenAI skip decorator
- MCP server automatically instantiates classes for instance methods
- Implement custom rule engine with time-based priority (REPO-125)
- Add code execution MCP following Anthropic's pattern (REPO-122 extension)
- Optimize MCP context usage following top engineers' best practices
- **REPO-140:** Add TimescaleDB integration infrastructure (Phases 1-2)
- **REPO-140:** Add CLI integration for TimescaleDB metrics tracking (Phase 3)
- **REPO-140:** Add TimescaleDB metrics tracking to MCP server
- **REPO-140:** Add metrics query CLI commands (Phase 4-5)
- Git + Graphiti integration for temporal code evolution tracking (REPO-139)
- AI-powered auto-fix system with human-in-the-loop approval
- Add security scanning and SBOM generation capabilities
- Add monorepo support and CI/CD auto-fix integration
- Add cross-detector collaboration and finding deduplication
- Integrate all detectors with graph enricher and deduplicator
- Add CLI enhancements for metadata retention and CI/CD auto-fix
- Enhance HTML reports with deduplication statistics
- Add graph detectors, Rust scanner, and security enhancements
- Add version management and release automation (REPO-68, REPO-69)
- Switch to maturin build for Rust extension bundling
- **security:** Enhance secrets scanner with performance and usability improvements
- **rust:** Add parallel MD5 file hashing (5-6x speedup)
- **rust:** Add AST-based cyclomatic complexity calculator (4.2x speedup)
- **rust:** Add LCOM calculation in Rust (6-9x speedup)
- **rust:** Add parallel cosine similarity for RAG (2-6x speedup)
- Add Lean 4 formal verification for health scoring
- **lean:** Add formal verification for security and thresholds
- **lean:** Enhance health score verification with grade proofs
- **lean:** Add formal verification for priority score, path safety, and risk amplification
- Add differential testing framework and formal verification docs
- **rust:** Add R0902 too-many-instance-attributes detector
- **rust:** Add R0903/R0904 pylint rules and integrate with detector
- **rust:** Add R0916 and R0401 pylint rules
- **rust:** Add R0911-R0915 pylint rules with zero warnings
- **rust:** Add W0611, W0612, W0613, C0301, C0302 pylint rules
- **rust:** Add 6 more pylint rules not covered by Ruff
- **rust:** Add graph algorithms for FalkorDB migration (REPO-192)
- **rust:** Implement harmonic centrality algorithm (REPO-198)
- Wire up Rust graph algorithms to detectors (REPO-200)
- **falkordb:** Add FalkorDBClient adapter for Redis-based graph DB
- **graph:** Add DatabaseClient abstraction and factory
- **falkordb:** Update detectors and pipeline for FalkorDB compatibility
- **ci:** Add FalkorDB to CI matrix, benchmarks, and Rust wheel builds (REPO-225, REPO-226)
- **detectors:** Add parallel execution for independent detectors (REPO-217)
- **detectors:** Parallelize Phase 2 dependent detectors (REPO-217)
- **rust:** Add error handling and comprehensive tests for graph algorithms (REPO-218, REPO-227)
- **rag:** Add query result caching with LRU eviction and TTL expiration
- **embeddings:** Add local sentence-transformers backend for free offline embeddings
- **detectors:** Add DataClumpsDetector for parameter group extraction (REPO-216)
- **detectors:** Add async antipattern, type hint coverage, and long parameter list detectors (REPO-228, REPO-229, REPO-231)
- **detectors:** Add generator misuse, message chain, and test smell detectors
- **mcp:** Add server generator, execution environment, and resources
- **observability:** Add observability module
- **rust:** Extend graph algorithms with new capabilities
- **rag:** Enhance retrieval with additional capabilities
- **rust:** Add duplicate code detection with 14x speedup over jscpd
- **mcp:** Add token-efficient utilities for data filtering, state, and skills
- **autofix:** Add multi-language support for code fix generation
- **autofix:** Add template-based fix system for deterministic code fixes
- **autofix:** Add style analysis for LLM-guided code generation
- **autofix:** Add learning feedback system for adaptive fix confidence
- **ml:** Add training data extraction from git history for bug prediction
- **ml:** Add Node2Vec embeddings and bug prediction model
- **ml:** Add multimodal fusion for text + graph embeddings
- **ml:** Add GraphSAGE for cross-project zero-shot defect prediction
- **detectors:** Add lazy class and refused bequest detectors, update embedding default
- **web:** Add Next.js 16 dashboard with auto-fix UI
- **db:** Add PostgreSQL models and Docker Compose for SaaS platform
- **auth:** Add Clerk authentication for frontend and backend
- **worker:** Add Celery + Redis background job processing
- **github:** Add GitHub App installation and OAuth handler
- **billing:** Add Stripe subscription and payment integration
- **deploy:** Add Fly.io deployment for API and Celery workers
- **web:** Add Sentry error tracking integration
- **auth:** Add Clerk webhook handler and production deployment fixes
- **auth:** Fetch user data from Clerk API on webhook events
- **sandbox:** Add E2B sandbox execution environment
- **autofix:** Add multi-level validation with sandbox support
- **api:** Add fix preview endpoint for sandbox validation
- **web:** Add fix preview panel UI component
- **sandbox:** Add metrics, alerts, tiers, and trial management
- **sandbox:** Add quota management and usage enforcement
- **autofix:** Add best-of-n generation with entitlements
- **embeddings:** Add DeepInfra backend for cheap Qwen3 embeddings
- **embeddings:** Add auto backend selection mode
- **ai:** Add hybrid search with contextual retrieval and reranking
- **gdpr:** Add GDPR compliance backend
- **web:** Add GDPR compliance UI
- **graph:** Enhance graph client and ingestion pipeline
- **email:** Add email service and templates
- **db:** Add email preferences models and migration
- **api:** Add notification routes and email preferences endpoints
- **workers:** Implement Celery analysis workers with progress tracking
- **webhooks:** Add Clerk organization and welcome email handlers
- **team:** Add team invitation feature with email notifications
- **graph:** Add multi-tenant graph database support (REPO-263)
- **cli:** Add authentication and payment tier enforcement (REPO-267)
- **api:** Add Sentry error tracking and health checks (REPO-271)
- **db:** Add migrations for graph tenant and GitHub columns
- **github:** Improve installation flow with redirect and auto-create org
- **dashboard:** Add health score gauge and analytics endpoint
- **web:** Add health score types, hooks and mock data
- **analyze:** Add manual Analyze button for GitHub repos (REPO-306)
- **api:** Add findings persistence and API endpoint
- **auth:** Move CLI auth state tokens to Redis
- **cache:** Add Redis caching layer for preview, scan, and skill caches
- **sandbox:** Add Redis-backed distributed session tracking
- **api:** Add quota overrides system for admin management
- **api:** Implement GitHub push webhook auto-analysis
- **api:** Implement hybrid audit logging system
- **db:** Add fixes persistence and repository patterns
- **dashboard:** Show analysis findings instead of fixes
- Add AI fix generation with Claude Opus 4.5
- **dashboard:** Add fix statistics widget and finding links
- **github:** Add PATCH endpoint for single repo toggle with optimistic UI
- **docs:** Add documentation site with API, CLI, and webhooks docs
- **db:** Add quality gates and webhooks tables with migrations
- **webhooks:** Add customer webhook system for event notifications
- **quality-gates:** Add quality gates service with configurable thresholds
- **github:** Add PR commenter and commit status integration
- **api:** Enhance API routes with organizations and improved endpoints
- **workers:** Enhance celery workers with improved hooks and tasks
- **cli:** Add new CLI commands and improve output formatting
- **web:** Redesign marketing pages and enhance dashboard UI
- **db:** Add database models and migrations for status, changelog, and API deprecation
- **api:** Add API versioning with v1/v2 routes and shared infrastructure
- **status:** Add status page feature with real-time monitoring
- **changelog:** Add changelog feature with RSS feed support
- **workers:** Add health check and changelog workers
- **github:** Add repository link to dashboard
- **auth:** Add Clerk API Key authentication and Open Core MCP server
- **billing:** Enforce api_access feature for code routes
- Re-enable billing enforcement for API routes
- **rust:** Add type inference and expand graph algorithms
- **api:** Improve routes and add cloud storage service
- **graph:** Update clients and add external labels support
- **detectors:** Add estimated_effort to all detector findings
- **rust:** Add parallel ML feature extraction and diff parsing (REPO-244, REPO-248)
- **detector:** Add SATD detector for TODO/FIXME/HACK comments (REPO-410)
- **security:** Add data flow graph and taint tracking (REPO-411)
- **security:** Add uv-secure as primary dependency scanner (REPO-413)
- **rust:** Add CFG analysis, incremental SCC, and contrastive learning

### Miscellaneous

- Ignore analysis outputs and temporary test files
- Temporarily disable coverage in pytest config
- Ignore benchmark.py temporary script
- Add PyYAML and tomli as optional dependencies
- Update dependencies for new features
- Add generated files to .gitignore
- Remove 61 unused imports detected by RuffImportDetector
- Update dependencies for collaboration and deduplication features
- **deps:** Update dependencies
- **web:** Clean up Sentry config from wizard
- Add E2B sandbox dependency and CLI preview support
- **deps:** Update dependencies
- **deps:** Update Python and frontend dependencies
- Ignore .env.production.local
- Add build timestamp to bust Docker cache
- **worker:** Increase VM memory from 1GB to 2GB
- **worker:** Increase VM memory from 2GB to 4GB
- Update dependencies and misc configuration
- Remove debug logging from billing enforcement
- Add CONTRIBUTING.md and LICENSE
- Update README and dependencies
- **deps:** Add aioboto3 to saas dependencies
- Ignore benchmark files

### Performance

- **rust:** Add combined pylint check with single parse
- **rust:** Parallelize graph algorithms with rayon
- **rust:** Optimize hashing and fix GIL release for parallel operations
- Optimize duplicate detection with hybrid algorithm
- **pipeline:** Optimize ingestion with batching and caching
- **web:** Parallelize GitHub API requests with Promise.all
- **email:** Parallelize email sending with asyncio.gather and aioboto3

### Refactoring

- Improve detector queries and relationship handling
- Rename project from Falkor to Repotoire
- Fix feature envy in SecretsScanner.scan_string
- Remove Rust pylint rules covered by Ruff
- **cli:** Use graph factory for database-agnostic ingestion
- Update core modules for improved functionality

### Security

- Move uv-secure and datasets to core dependencies

### Testing

- Add integration tests and fixtures
- Add comprehensive test coverage for new features
- Add comprehensive tests for RAG components
- Comprehensive MCP server validation - all tests passed ✅
- **REPO-140:** Add comprehensive TimescaleDB integration tests (Phase 6)
- Add CI integration tests for auto-fix system
- **detectors:** Add generator misuse tests and fix test smell tests
- **sandbox:** Add unit and integration tests for sandbox functionality
- **sandbox:** Add unit tests for quota management
- **autofix:** Add tests for best-of-n and entitlements
- Add tests for AI retrieval and GDPR services
- Add email service and notification route tests
- **e2e:** Add Playwright E2E testing infrastructure
- Add comprehensive integration tests for API routes
- Update integration tests for new functionality
- **type-inference:** Add strict metrics validation and regression tests

### Debug

- Add logging to billing enforcement

### Deps

- Add sentence-transformers and accelerate to core deps

### Temp

- Bypass billing check to debug route issue

---
Generated by [git-cliff](https://git-cliff.org)
