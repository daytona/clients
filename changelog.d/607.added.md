**Charts from interpreter runs** — a matplotlib figure that a Python
`interpreter.run()` displays now comes back with its data next to its image:
`InterpreterOutput.chart` and every chart of the run in
`InterpreterResult.charts`, typed as `LineChart`, `ScatterChart`, `BarChart`,
`PieChart`, `BoxAndWhiskerChart`, `CompositeChart` (one chart per subplot) or
`UnknownChart`. It covers `plt.show()`, a figure returned as the last
expression, pandas and seaborn plots, and figures still open when the cell
ends.
