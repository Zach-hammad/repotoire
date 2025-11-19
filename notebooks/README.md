# Falkor Jupyter Notebooks

This directory contains example Jupyter notebooks demonstrating Falkor's capabilities.

## Notebooks

### 1. Basic Analysis (`01_basic_analysis.ipynb`)
**Recommended starting point for new users**

Learn the fundamental Falkor workflow:
- Setting up connections to Neo4j
- Ingesting a codebase into the knowledge graph
- Running analysis and interpreting results
- Understanding health scores and metrics
- Exploring findings and getting actionable insights
- Exporting reports in JSON and HTML formats

**Prerequisites**: Neo4j running, basic Python knowledge

**Time**: 15-20 minutes

---

### 2. Custom Cypher Queries (`02_custom_queries.ipynb`)
**For users who want to explore the graph directly**

Write custom queries to gain deeper insights:
- Basic graph traversal patterns
- Finding dependencies and coupling
- Analyzing complexity hotspots
- Discovering architectural patterns
- Advanced analysis (cohesion, centrality, etc.)
- Query performance optimization tips

**Prerequisites**: Basic understanding of Cypher (Neo4j query language)

**Time**: 30-45 minutes

---

### 3. Visualization (`03_visualization.ipynb`)
**For visual exploration of the codebase**

Create compelling visualizations:
- Neo4j Browser visualization
- NetworkX graph visualization
- Complexity heatmaps with matplotlib
- Interactive graphs with Plotly
- Circular dependency visualization
- Exporting graphs for external tools (Gephi, yEd)

**Prerequisites**: `networkx`, `matplotlib`, `plotly` installed

**Time**: 20-30 minutes

---

### 4. Batch Analysis (`04_batch_analysis.ipynb`)
**For teams managing multiple projects**

Analyze and compare multiple codebases:
- Batch processing multiple projects
- Comparing health scores across projects
- Identifying best and worst practices
- Generating executive summaries
- Tracking metrics over time
- Creating comparative dashboards

**Prerequisites**: Multiple codebases to analyze

**Time**: 30-45 minutes (depending on project count)

---

## Getting Started

### Installation

1. **Install Falkor with Jupyter support**:
   ```bash
   pip install -e ".[dev]"
   pip install jupyter networkx matplotlib plotly pandas
   ```

2. **Start Neo4j**:
   ```bash
   docker run --name falkor-neo4j \
       -p 7474:7474 -p 7687:7687 \
       -e NEO4J_AUTH=neo4j/your-password \
       neo4j:latest
   ```

3. **Configure Falkor** (create `.env` or `.falkorrc`):
   ```bash
   # Copy example config
   cp .env.example .env

   # Edit with your Neo4j credentials
   FALKOR_NEO4J_PASSWORD=your-password
   ```

### Running Notebooks

1. **Start Jupyter**:
   ```bash
   jupyter notebook
   ```

2. **Open a notebook** from the browser interface

3. **Run cells sequentially** using Shift+Enter

### Quick Start Path

New to Falkor? Follow this learning path:

1. Start with `01_basic_analysis.ipynb` to understand the workflow
2. Read the generated reports to understand findings
3. Explore `02_custom_queries.ipynb` for deeper analysis
4. Try `03_visualization.ipynb` for visual insights
5. Use `04_batch_analysis.ipynb` for multi-project analysis

---

## Configuration

All notebooks use Falkor's configuration system. Configure via:

- **Environment variables**: `FALKOR_NEO4J_URI`, `FALKOR_NEO4J_PASSWORD`, etc.
- **Config file**: `.falkorrc` (YAML) or `falkor.toml`
- **Direct parameters**: Pass to `Neo4jClient()` constructor

See `CONFIG.md` in the project root for full configuration reference.

---

## Common Issues

### Neo4j Connection Errors

**Problem**: `ServiceUnavailable: Could not connect to Neo4j`

**Solution**:
- Verify Neo4j is running: `docker ps`
- Check URI and password in config
- Try connecting with Neo4j Browser: http://localhost:7474

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'falkor'`

**Solution**:
- Install Falkor in development mode: `pip install -e .`
- Verify installation: `python -c "import falkor; print(falkor.__file__)"`

### Visualization Issues

**Problem**: Plots not displaying

**Solution**:
- Install visualization packages: `pip install matplotlib plotly networkx`
- For Jupyter, try: `%matplotlib inline` at notebook start
- Restart Jupyter kernel if needed

---

## Output Files

Notebooks generate various output files:

- `health_report.json` - Full health report in JSON format
- `health_report.html` - Interactive HTML report
- `dependency_graph.gexf` - Graph export for Gephi
- `dependency_graph.graphml` - Graph export for yEd
- `reports/` - Directory with batch analysis results
- `reports/comparison.csv` - Project comparison table
- `reports/executive_summary.json` - Executive summary

---

## Tips for Production Use

### Performance

- Limit query result sizes with `LIMIT`
- Use indexes for frequently queried properties
- Close connections when done: `db.close()`
- Clear graph between analyses if memory-constrained

### Automation

- Convert notebooks to Python scripts: `jupyter nbconvert --to script notebook.ipynb`
- Schedule with cron or CI/CD pipelines
- Use headless execution: `jupyter nbconvert --execute --to html notebook.ipynb`
- Store results in time-series database for tracking

### Collaboration

- Share notebooks via GitHub, JupyterHub, or Google Colab
- Export to HTML for stakeholders: `jupyter nbconvert --to html notebook.ipynb`
- Use version control for notebook changes
- Document assumptions and context in markdown cells

---

## Additional Resources

- **Falkor Documentation**: See `README.md` and `CLAUDE.md`
- **Neo4j Cypher Manual**: https://neo4j.com/docs/cypher-manual/
- **Jupyter Documentation**: https://jupyter.org/documentation
- **Example Datasets**: Use your own codebases or public repos

---

## Contributing

Found an issue or have an improvement?

1. Check existing notebooks for similar patterns
2. Add comprehensive markdown explanations
3. Include error handling and logging
4. Test with different project sizes
5. Submit a pull request with your improvements

---

## License

These notebooks are part of the Falkor project and licensed under MIT License.

---

## Support

- **Issues**: https://github.com/yourusername/falkor/issues
- **Documentation**: See project README.md
- **Community**: Join our discussions

---

**Happy analyzing! 🚀**
