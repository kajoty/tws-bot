"""
Performance-Analyse und Visualisierung für den IB Trading Bot.
Erstellt Charts für Equity Curve, Drawdown und Trading-Signale.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import Optional, Dict
import logging
import os
import config

logger = logging.getLogger(__name__)


class PerformanceAnalyzer:
    """Analysiert und visualisiert Trading-Performance."""

    def __init__(self, output_dir: str = config.PLOT_DIR):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        plt.style.use('seaborn-v0_8-darkgrid')

    def plot_performance(self, performance_df: pd.DataFrame, trades_df: Optional[pd.DataFrame] = None,
                        save_path: Optional[str] = None) -> str:
        """Erstellt Performance-Report mit Equity Curve, Drawdown und Returns."""
        if performance_df.empty:
            logger.warning("Keine Performance-Daten zum Plotten")
            return ""

        try:
            fig, axes = plt.subplots(3, 1, figsize=(14, 10))
            fig.suptitle('Trading Bot Performance Analysis', fontsize=16, fontweight='bold')

            self._plot_equity_curve(performance_df, axes[0], trades_df)
            self._plot_drawdown(performance_df, axes[1])
            self._plot_returns_distribution(performance_df, axes[2])

            plt.tight_layout()

            if save_path is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                save_path = os.path.join(self.output_dir, f'performance_{timestamp}.png')

            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            plt.close()

            logger.info(f"Performance-Plot gespeichert: {save_path}")
            return save_path

        except Exception as e:
            logger.error(f"Fehler beim Erstellen des Performance-Plots: {e}")
            return ""

    def _plot_equity_curve(self, df: pd.DataFrame, ax: plt.Axes, trades_df: Optional[pd.DataFrame] = None):
        """Plottet die Equity Curve mit Trade-Markierungen."""
        df = df.copy()
        x = pd.to_datetime(df['timestamp']) if 'timestamp' in df.columns else range(len(df))

        ax.plot(x, df['equity'], linewidth=2, label='Portfolio Value', color='#2E86C1')
        ax.fill_between(x, df['equity'], alpha=0.3, color='#2E86C1')
        ax.axhline(y=df['equity'].iloc[0], color='gray', linestyle='--', linewidth=1,
                   label=f'Initial: ${df["equity"].iloc[0]:,.0f}')

        if trades_df is not None and not trades_df.empty:
            trades_df = trades_df.copy()
            if 'timestamp' in trades_df.columns:
                trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
                buys = trades_df[trades_df['action'] == 'BUY']
                sells = trades_df[trades_df['action'] == 'SELL']
                if not buys.empty:
                    ax.scatter(buys['timestamp'], buys['price'], color='green', marker='^',
                             s=100, label='Buy', zorder=5, alpha=0.7)
                if not sells.empty:
                    ax.scatter(sells['timestamp'], sells['price'], color='red', marker='v',
                             s=100, label='Sell', zorder=5, alpha=0.7)

        ax.set_title('Equity Curve', fontsize=12, fontweight='bold')
        ax.set_ylabel('Portfolio Value ($)', fontsize=10)
        ax.legend(loc='best')
        ax.grid(True, alpha=0.3)
        if 'timestamp' in df.columns:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    def _plot_drawdown(self, df: pd.DataFrame, ax: plt.Axes):
        """Plottet die Drawdown-Analyse."""
        df = df.copy()
        cumulative_max = df['equity'].cummax()
        drawdown = (df['equity'] - cumulative_max) / cumulative_max * 100
        x = pd.to_datetime(df['timestamp']) if 'timestamp' in df.columns else range(len(df))

        ax.fill_between(x, drawdown, 0, where=(drawdown < 0), color='red', alpha=0.5, label='Drawdown')
        ax.plot(x, drawdown, linewidth=1.5, color='darkred')
        ax.axhline(y=0, color='gray', linestyle='-', linewidth=1)

        max_dd = drawdown.min()
        max_dd_idx = drawdown.idxmin()
        ax.annotate(f'Max DD: {max_dd:.2f}%',
                   xy=(x.iloc[max_dd_idx] if isinstance(x, pd.Series) else max_dd_idx, max_dd),
                   xytext=(10, -20), textcoords='offset points',
                   bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.7),
                   arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'))

        ax.set_title('Drawdown Analysis', fontsize=12, fontweight='bold')
        ax.set_ylabel('Drawdown (%)', fontsize=10)
        ax.legend(loc='lower right')
        ax.grid(True, alpha=0.3)
        if 'timestamp' in df.columns:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    def _plot_returns_distribution(self, df: pd.DataFrame, ax: plt.Axes):
        """Plottet die Verteilung der Returns."""
        df = df.copy()
        df['returns'] = df['equity'].pct_change() * 100
        df = df.dropna()

        if df.empty or len(df) < 2:
            ax.text(0.5, 0.5, 'Nicht genügend Daten', ha='center', va='center', transform=ax.transAxes)
            return

        ax.hist(df['returns'], bins=50, color='#5DADE2', edgecolor='black', alpha=0.7)
        mean_return = df['returns'].mean()
        median_return = df['returns'].median()
        ax.axvline(mean_return, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_return:.2f}%')
        ax.axvline(median_return, color='green', linestyle='--', linewidth=2, label=f'Median: {median_return:.2f}%')

        ax.set_title('Daily Returns Distribution', fontsize=12, fontweight='bold')
        ax.set_xlabel('Daily Return (%)', fontsize=10)
        ax.set_ylabel('Frequency', fontsize=10)
        ax.legend(loc='best')
        ax.grid(True, alpha=0.3, axis='y')

    def calculate_metrics(self, performance_df: pd.DataFrame) -> Dict:
        """Berechnet Performance-Metriken: Sharpe, Sortino, Drawdown, etc."""
        if performance_df.empty:
            return {}

        try:
            df = performance_df.copy()
            initial_equity = df['equity'].iloc[0]
            final_equity = df['equity'].iloc[-1]
            total_return = ((final_equity - initial_equity) / initial_equity) * 100

            cumulative_max = df['equity'].cummax()
            drawdown = (df['equity'] - cumulative_max) / cumulative_max * 100
            max_drawdown = drawdown.min()

            df['returns'] = df['equity'].pct_change()
            df = df.dropna()

            sharpe_ratio = 0.0
            if len(df) > 1 and df['returns'].std() > 0:
                sharpe_ratio = (df['returns'].mean() / df['returns'].std()) * np.sqrt(252)

            negative_returns = df[df['returns'] < 0]['returns']
            sortino_ratio = 0.0
            if len(negative_returns) > 0 and negative_returns.std() > 0:
                sortino_ratio = (df['returns'].mean() / negative_returns.std()) * np.sqrt(252)

            return {
                'initial_equity': initial_equity,
                'final_equity': final_equity,
                'total_return_pct': total_return,
                'max_drawdown_pct': max_drawdown,
                'sharpe_ratio': sharpe_ratio,
                'sortino_ratio': sortino_ratio,
                'avg_daily_return_pct': df['returns'].mean() * 100,
                'volatility_pct': df['returns'].std() * 100,
                'best_day_pct': df['returns'].max() * 100,
                'worst_day_pct': df['returns'].min() * 100
            }

        except Exception as e:
            logger.error(f"Fehler bei Metrikberechnung: {e}")
            return {}

    def print_summary(self, metrics: Dict):
        """Gibt formatierte Zusammenfassung aus."""
        if not metrics:
            return

        print("\n" + "="*60)
        print(" PERFORMANCE SUMMARY")
        print("="*60)
        print(f" Initial Equity:        ${metrics['initial_equity']:,.2f}")
        print(f" Final Equity:          ${metrics['final_equity']:,.2f}")
        print(f" Total Return:          {metrics['total_return_pct']:.2f}%")
        print(f" Max Drawdown:          {metrics['max_drawdown_pct']:.2f}%")
        print(f" Sharpe Ratio:          {metrics['sharpe_ratio']:.2f}")
        print(f" Sortino Ratio:         {metrics['sortino_ratio']:.2f}")
        print(f" Avg Daily Return:      {metrics['avg_daily_return_pct']:.3f}%")
        print(f" Daily Volatility:      {metrics['volatility_pct']:.3f}%")
        print(f" Best Day:              {metrics['best_day_pct']:.2f}%")
        print(f" Worst Day:             {metrics['worst_day_pct']:.2f}%")
        print("="*60 + "\n")
