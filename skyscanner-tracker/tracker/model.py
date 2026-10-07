"""Μοντέλο πρόβλεψης τιμής.

Τι προβλέπουμε
--------------
Για κάθε ενεργή πτήση με τρέχουσα τιμή p, για κάθε μελλοντικό slot των 6 ωρών
(+6h, +12h, ... μέχρι την αναχώρηση) δίνουμε τρία ποσοστημόρια της τιμής:
  q10 (αισιόδοξο), q50 (πιο πιθανό), q90 (απαισιόδοξο)
και την πιθανότητα να εμφανιστεί αισθητά χαμηλότερη τιμή πριν την πτήση.

Ιεραρχία μεθόδων (επιλέγεται η καλύτερη ΜΕ ΒΑΣΗ ΤΟ BACKTEST, όχι «στα τυφλά»)
-------------------------------------------------------------------------------
1. prior      : χωρίς δεδομένα — ουδέτερη υπόθεση με ήπια άνοδο τις 3 τελευταίες μέρες
2. empirical  : ποσοστημόρια των πραγματικών μεταβολών από τα δικά σου δεδομένα,
                ανά «ώρες μέχρι την πτήση» × «ορίζοντας» (με shrinkage προς το prior)
3. gbm        : Gradient Boosting quantile regression με χαρακτηριστικά πτήσης
                (ώρες μέχρι αναχώρηση, ημέρα/ώρα, αεροπορική, πρόσφατη τάση,
                θέση της τιμής σε σχέση με το ελάχιστο της πτήσης και της ημέρας)
Το gbm χρησιμοποιείται μόνο όταν (α) υπάρχουν αρκετά δεδομένα και (β) έχει
μικρότερο σφάλμα από το empirical σε out-of-sample backtest.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.neighbors import NearestNeighbors

from .config import LOCAL_TZ

QUANTILES = (0.1, 0.5, 0.9)
STEP_H = 6
MAX_H = 240
H2D_BINS = [0, 24, 48, 72, 120, 168, 1e9]
H_BINS = [0, 6, 12, 24, 48, 96, 1e9]
SHRINK_K = 30.0

FEATURES = ["h2d", "horizon", "h2d_end", "dep_dow", "dep_hour", "carrier_cat", "route_cat",
            "log_price", "ret_prev", "gap_prev", "ret_24h", "rel_min", "rel_max", "rel_day",
            "n_obs", "age_h"]
CAT_FEATURES = ["carrier_cat", "route_cat", "dep_dow"]
DROP_FEATURES = [f for f in FEATURES if f not in ("horizon", "h2d_end")]


# ===================================================================== prep
def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Προσθέτει χρόνους και χαρακτηριστικά σε κάθε παρατήρηση."""
    if df.empty:
        return df.assign(t=pd.Series(dtype="datetime64[ns, UTC]"))
    d = df.copy()
    d["t"] = pd.to_datetime(d["observed_at"], utc=True)
    dep_naive = pd.to_datetime(d["dep_local"])
    d["dep_utc"] = (dep_naive.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="shift_forward")
                    .dt.tz_convert("UTC"))
    d = d.dropna(subset=["dep_utc"])
    d["h2d"] = (d["dep_utc"] - d["t"]).dt.total_seconds() / 3600
    d = d[d["h2d"] > 0].copy()
    d["dep_dow"] = dep_naive.loc[d.index].dt.dayofweek
    d["dep_hour"] = dep_naive.loc[d.index].dt.hour + dep_naive.loc[d.index].dt.minute / 60
    d["log_price"] = np.log(d["price"].astype(float))
    d["carrier_cat"] = d["carrier_code"].replace("", "XX").astype("category").cat.codes
    d["route_cat"] = d["route"].astype("category").cat.codes

    d = d.sort_values(["flight_id", "t"]).reset_index(drop=True)
    g = d.groupby("flight_id", sort=False)
    d["n_obs"] = g.cumcount()
    prev_lp = g["log_price"].shift(1)
    prev_t = g["t"].shift(1)
    d["ret_prev"] = (d["log_price"] - prev_lp).fillna(0.0)
    d["gap_prev"] = ((d["t"] - prev_t).dt.total_seconds() / 3600).fillna(0.0)
    d["rel_min"] = d["log_price"] - g["log_price"].cummin()
    d["rel_max"] = d["log_price"] - g["log_price"].cummax()
    d["age_h"] = (d["t"] - g["t"].transform("min")).dt.total_seconds() / 3600

    # επιστροφή 24 ωρών (με ανοχή 4 ωρών)
    look = d[["flight_id", "t", "log_price"]].copy()
    look["t"] = look["t"] + pd.Timedelta(hours=24)
    look = look.rename(columns={"log_price": "lp_24"}).sort_values("t")
    merged = pd.merge_asof(d[["flight_id", "t"]].reset_index().sort_values("t"), look,
                           on="t", by="flight_id", tolerance=pd.Timedelta(hours=4), direction="nearest")
    merged = merged.set_index("index").sort_index()
    d["ret_24h"] = (d["log_price"] - merged["lp_24"]).fillna(0.0)

    # θέση σε σχέση με τη φθηνότερη πτήση της ίδιας μέρας/διαδρομής στον ίδιο κύκλο
    day_min = d.groupby(["route", "dep_date", "observed_at"])["log_price"].transform("min")
    d["rel_day"] = d["log_price"] - day_min
    return d


def make_pairs(d: pd.DataFrame, max_pairs: int = 400_000, seed: int = 0) -> pd.DataFrame:
    """(παρατήρηση i, μεταγενέστερη παρατήρηση j) της ίδιας πτήσης -> y = log(p_j/p_i)."""
    if d.empty:
        return pd.DataFrame()
    cols = ["flight_id", "t", "price"] + [f for f in FEATURES if f not in ("horizon", "h2d_end")]
    base = d[list(dict.fromkeys(cols))]
    later = d[["flight_id", "t", "log_price", "price"]].rename(
        columns={"t": "t_j", "log_price": "lp_j", "price": "price_j"})
    p = base.merge(later, on="flight_id")
    hours = (p["t_j"] - p["t"]).dt.total_seconds() / 3600
    step = (hours / STEP_H).round() * STEP_H
    p = p[(hours > 2) & ((hours - step).abs() <= 2.5) & (step <= MAX_H) & (step < p["h2d"])].copy()
    p["horizon"] = step.loc[p.index]
    p["h2d_end"] = p["h2d"] - p["horizon"]
    p["y"] = p["lp_j"] - p["log_price"]
    if len(p) > max_pairs:
        p = p.sample(max_pairs, random_state=seed)
    return p.reset_index(drop=True)


def drop_labels(d: pd.DataFrame, latest_t: pd.Timestamp, thr_eur: float, thr_pct: float) -> pd.DataFrame:
    """Για πτήσεις που έχουν ήδη φύγει: εμφανίστηκε αργότερα τιμή <= p - κατώφλι;"""
    done = d[d["dep_utc"] <= latest_t]
    if done.empty:
        return pd.DataFrame()
    done = done.sort_values(["flight_id", "t"])
    # ελάχιστο ΜΕΛΛΟΝΤΙΚΗΣ τιμής (αυστηρά μετά από αυτή την παρατήρηση)
    fut_min = (done.iloc[::-1].groupby("flight_id")["price"].cummin().iloc[::-1])
    fut_min = done.assign(fm=fut_min).groupby("flight_id")["fm"].shift(-1)
    out = done.assign(future_min=fut_min).dropna(subset=["future_min"]).copy()
    thr = np.maximum(thr_eur, out["price"] * thr_pct)
    out["drop"] = (out["future_min"] <= out["price"] - thr).astype(int)
    final_all = done.groupby("flight_id")["price"].last()
    out["final_price"] = out["flight_id"].map(final_all)
    out["best_saving"] = (out["price"] - out["future_min"]).clip(lower=0)
    out["final_change"] = out["final_price"] - out["price"]
    return out


# ===================================================================== methods
def _prior_q(h2d: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Ουδέτερο prior: ήπια άνοδος όσο το παράθυρο πέφτει στις τελευταίες 72 ώρες."""
    h2d_end = h2d - h
    hours_in_last72 = np.clip(np.minimum(h2d, 72) - np.clip(h2d_end, 0, 72), 0, None)
    med = 0.0015 * hours_in_last72
    sigma = np.minimum(0.03 * np.sqrt(h / STEP_H), 0.35)
    return np.stack([med - 1.28 * sigma * 0.8, med, med + 1.28 * sigma * 1.2], axis=1)


def _bin(x, edges):
    return np.clip(np.digitize(x, edges[1:-1], right=False), 0, len(edges) - 2)


@dataclass
class EmpiricalModel:
    table: dict = field(default_factory=dict)  # (h2d_bin, h_bin) -> (n, q10, q50, q90)

    def fit(self, pairs: pd.DataFrame):
        self.table = {}
        if pairs.empty:
            return self
        b1 = _bin(pairs["h2d"].values, H2D_BINS)
        b2 = _bin(pairs["horizon"].values, H_BINS)
        tmp = pd.DataFrame({"b1": b1, "b2": b2, "y": pairs["y"].values})
        for (i, j), grp in tmp.groupby(["b1", "b2"]):
            qs = np.quantile(grp["y"].values, QUANTILES)
            self.table[(int(i), int(j))] = (len(grp), *qs)
        return self

    def predict(self, h2d, h) -> np.ndarray:
        h2d = np.asarray(h2d, float)
        h = np.asarray(h, float)
        prior = _prior_q(h2d, h)
        b1 = _bin(h2d, H2D_BINS)
        b2 = _bin(h, H_BINS)
        out = prior.copy()
        for k in range(len(h2d)):
            cell = self.table.get((int(b1[k]), int(b2[k])))
            if cell:
                n = cell[0]
                w = n / (n + SHRINK_K)
                out[k] = w * np.array(cell[1:]) + (1 - w) * prior[k]
        return np.sort(out, axis=1)


ANALOG_FEATURES = ["h2d", "horizon", "dep_hour", "dep_dow", "log_price", "ret_prev", "ret_24h",
                   "rel_min", "rel_max", "rel_day", "carrier_cat"]
ANALOG_WEIGHTS = np.array([3.0, 3.0, 0.7, 0.5, 1.0, 1.0, 1.0, 1.5, 1.0, 1.5, 0.8])


class _Scaler:
    def fit(self, X: np.ndarray, weights: np.ndarray):
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-6
        self.w = weights
        return self

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd * self.w


class AnalogModel:
    """«Παρόμοιες περιπτώσεις» (k-nearest neighbours): βρίσκει στο ιστορικό τις
    καταστάσεις που μοιάζουν περισσότερο με την τωρινή (ώρες ως την πτήση, τάση,
    θέση τιμής, αεροπορική, ώρα πτήσης…) και κοιτάζει τι έγινε μετά σε αυτές."""

    def __init__(self, k: int = 80, max_train: int = 150_000):
        self.k, self.max_train = k, max_train

    def fit(self, pairs: pd.DataFrame):
        p = pairs.sample(self.max_train, random_state=0) if len(pairs) > self.max_train else pairs
        X = p[ANALOG_FEATURES].to_numpy(float)
        self.scale = _Scaler().fit(X, ANALOG_WEIGHTS)
        self.y = p["y"].to_numpy(float)
        self.k_eff = min(self.k, len(p))
        self.nn = NearestNeighbors(n_neighbors=self.k_eff).fit(self.scale(X))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        _, idx = self.nn.kneighbors(self.scale(X[ANALOG_FEATURES].to_numpy(float)))
        ys = self.y[idx]
        return np.sort(np.quantile(ys, QUANTILES, axis=1).T, axis=1)


class EnsembleModel:
    """Μέσος όρος ποσοστημορίων από πολλά μοντέλα — συνήθως πιο σταθερός από το καθένα."""

    def __init__(self, members: list):
        self.members = members

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.sort(np.mean([predict_any(m, X) for m in self.members], axis=0), axis=1)


def predict_any(m, X: pd.DataFrame) -> np.ndarray:
    if isinstance(m, EmpiricalModel):
        return m.predict(X["h2d"].values, X["horizon"].values)
    return m.predict(X)


class SimilarCases:
    """Live ανάλυση: για μια πτήση τώρα, τι έγινε σε παρόμοιες πτήσεις που έχουν ήδη φύγει."""
    FEATS = ["h2d", "dep_hour", "dep_dow", "log_price", "ret_prev", "ret_24h", "rel_min", "rel_max",
             "rel_day", "carrier_cat"]
    W = np.array([3.0, 0.7, 0.5, 1.0, 1.0, 1.0, 1.5, 1.0, 1.5, 0.8])

    def __init__(self, lab: pd.DataFrame, k: int = 60):
        X = lab[self.FEATS].to_numpy(float)
        self.scale = _Scaler().fit(X, self.W)
        self.lab = lab.reset_index(drop=True)
        self.k = min(k, len(lab))
        self.nn = NearestNeighbors(n_neighbors=self.k).fit(self.scale(X))

    def query(self, rows: pd.DataFrame) -> list[dict]:
        _, idx = self.nn.kneighbors(self.scale(rows[self.FEATS].to_numpy(float)))
        out = []
        for ii in idx:
            nb = self.lab.iloc[ii]
            out.append({
                "n": int(len(nb)),
                "n_flights": int(nb["flight_id"].nunique()),
                "pct_drop": round(float(nb["drop"].mean()), 3),
                "avg_saving_when_drop": round(float(nb.loc[nb["drop"] == 1, "best_saving"].mean()), 2)
                if nb["drop"].any() else 0.0,
                "pct_final_higher": round(float((nb["final_change"] > 1).mean()), 3),
                "median_final_change": round(float(nb["final_change"].median()), 2),
            })
        return out


class GBMModel:
    def __init__(self):
        self.models = {}

    def fit(self, pairs: pd.DataFrame):
        X = pairs[FEATURES]
        for q in QUANTILES:
            m = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, max_iter=250, learning_rate=0.05,
                max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0,
                categorical_features=[FEATURES.index(c) for c in CAT_FEATURES], random_state=0)
            m.fit(X, pairs["y"].values)
            self.models[q] = m
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        out = np.stack([self.models[q].predict(X[FEATURES]) for q in QUANTILES], axis=1)
        return np.sort(out, axis=1)


def apply_scale(q: np.ndarray, s: float) -> np.ndarray:
    out = q.copy()
    out[:, 0] = q[:, 1] - s * (q[:, 1] - q[:, 0])
    out[:, 2] = q[:, 1] + s * (q[:, 2] - q[:, 1])
    return out


def calibrate_scale(cal: pd.DataFrame, q: np.ndarray, target: float = 0.8) -> float:
    """Split-conformal-style: βρίσκει πόσο να ανοίξει/κλείσει το διάστημα q10–q90
    ώστε να περιέχει ~80% των πραγματικών τιμών σε δεδομένα που δεν είδε το μοντέλο."""
    y = cal["y"].values
    best, best_err = 1.0, 1e9
    for s in np.linspace(0.5, 4.0, 36):
        qs = apply_scale(q, s)
        cov = np.mean((y >= qs[:, 0] - 1e-9) & (y <= qs[:, 2] + 1e-9))
        err = abs(cov - target)
        if err < best_err - 1e-9:
            best, best_err = float(s), err
    return best


def _by_horizon(test: pd.DataFrame, q: np.ndarray) -> list[dict]:
    p0, pj = test["price"].values, test["price_j"].values
    med = p0 * np.exp(q[:, 1])
    out = []
    for lo, hi, label in [(0, 12, "6–12 ώρες"), (13, 24, "18–24 ώρες"), (25, 72, "1–3 μέρες"),
                          (73, 168, "3–7 μέρες"), (169, 1e9, "7+ μέρες")]:
        m = (test["horizon"].values >= lo) & (test["horizon"].values <= hi)
        if m.sum() >= 20:
            out.append({"label": label, "mae_eur": round(float(np.mean(np.abs(med[m] - pj[m]))), 2),
                        "naive_mae_eur": round(float(np.mean(np.abs(p0[m] - pj[m]))), 2),
                        "n": int(m.sum())})
    return out


def pinball(y, qpred) -> float:
    loss = 0.0
    for k, q in enumerate(QUANTILES):
        e = y - qpred[:, k]
        loss += np.mean(np.maximum(q * e, (q - 1) * e))
    return float(loss / len(QUANTILES))


def _metrics(test: pd.DataFrame, qpred: np.ndarray) -> dict:
    p0 = test["price"].values
    pj = test["price_j"].values
    med = p0 * np.exp(qpred[:, 1])
    lo, hi = p0 * np.exp(qpred[:, 0]), p0 * np.exp(qpred[:, 2])
    true_dir = np.sign(np.where(np.abs(pj - p0) < 1.0, 0, pj - p0))
    pred_dir = np.sign(np.where(np.abs(med - p0) < 1.0, 0, med - p0))
    return {
        "pinball": round(pinball(test["y"].values, qpred), 5),
        "mae_eur": round(float(np.mean(np.abs(med - pj))), 2),
        "coverage80": round(float(np.mean((pj >= lo - 0.01) & (pj <= hi + 0.01))), 3),
        "direction_acc": round(float(np.mean(true_dir == pred_dir)), 3),
        "n": int(len(test)),
    }


# ===================================================================== train+select
@dataclass
class Trained:
    method: str
    model: object
    backtest: dict
    n_pairs: int
    data_days: float
    drop_method: str
    drop_model: object
    drop_backtest: dict
    drop_base: dict
    interval_scale: float = 1.0
    similar: object = None


def train(d: pd.DataFrame, cfg) -> Trained:
    pairs = make_pairs(d)
    n_pairs = len(pairs)
    data_days = 0.0 if d.empty else (d["t"].max() - d["t"].min()).total_seconds() / 86400
    backtest: dict = {}
    method, model = "prior", EmpiricalModel()

    scale = 1.0
    if n_pairs >= 50:
        method, model = "empirical", EmpiricalModel().fit(pairs)
        times = np.sort(d["t"].unique())
        if len(times) >= 12:
            # Χρονικό backtest: εκπαίδευση στο παρελθόν, βαθμονόμηση στο 75-87%, αξιολόγηση στο τελευταίο 13%
            cutoff = pd.Timestamp(times[int(len(times) * 0.75)])
            cal_end = pd.Timestamp(times[int(len(times) * 0.875)])
            train_p = pairs[pairs["t_j"] < cutoff]
            cal_p = pairs[(pairs["t"] >= cutoff) & (pairs["t"] < cal_end)]
            eval_p = pairs[pairs["t"] >= cal_end]
            if len(train_p) >= 50 and len(cal_p) >= 30 and len(eval_p) >= 30:
                naive_q = _prior_q(eval_p["h2d"].values, eval_p["horizon"].values)
                naive_q[:, 1] = 0.0
                backtest["naive"] = _metrics(eval_p, np.sort(naive_q, axis=1))
                cands = {"empirical": EmpiricalModel().fit(train_p)}
                if len(train_p) >= 300:
                    cands["analog"] = AnalogModel().fit(train_p)
                if n_pairs >= cfg.min_pairs_for_ml and data_days >= cfg.min_days_for_ml and len(train_p) >= 500:
                    cands["gbm"] = GBMModel().fit(train_p)
                if len(cands) >= 2:
                    cands["ensemble"] = EnsembleModel(list(cands.values()))
                scales = {}
                for name, m in cands.items():
                    s = calibrate_scale(cal_p, predict_any(m, cal_p))
                    scales[name] = s
                    q_eval = apply_scale(predict_any(m, eval_p), s)
                    backtest[name] = _metrics(eval_p, q_eval)
                    backtest[name]["interval_scale"] = round(s, 2)
                    backtest[name]["by_horizon"] = _by_horizon(eval_p, q_eval)
                best = min(cands, key=lambda k: backtest[k]["pinball"])
                scale = scales[best]
                method = best
                # επανεκπαίδευση της επιλεγμένης μεθόδου σε ΟΛΑ τα δεδομένα
                full = {"empirical": lambda: EmpiricalModel().fit(pairs),
                        "analog": lambda: AnalogModel().fit(pairs),
                        "gbm": lambda: GBMModel().fit(pairs)}
                if best == "ensemble":
                    model = EnsembleModel([full[k]() for k in cands if k != "ensemble"])
                else:
                    model = full[best]()
                backtest["cutoff"] = str(cutoff)
                backtest["eval_from"] = str(cal_end)

    # ----- πιθανότητα πτώσης
    latest = d["t"].max() if not d.empty else None
    lab = drop_labels(d, latest, cfg.drop_threshold_eur, cfg.drop_threshold_pct) if latest is not None else pd.DataFrame()
    drop_method, drop_model, drop_bt, drop_base = "from_quantiles", None, {}, {}
    if len(lab) >= 100:
        drop_method = "empirical"
        b = _bin(lab["h2d"].values, H2D_BINS)
        rates = pd.DataFrame({"b": b, "y": lab["drop"].values}).groupby("b")["y"].agg(["sum", "count"])
        drop_base = {int(k): (float(r["sum"]), float(r["count"])) for k, r in rates.iterrows()}
        flights = lab["flight_id"].unique()
        if len(lab) >= 600 and len(flights) >= 40:
            # backtest: εκπαίδευση σε πτήσεις που έφυγαν νωρίτερα, τεστ στις πιο πρόσφατες
            order = lab.groupby("flight_id")["dep_utc"].first().sort_values()
            cut = order.index[: int(len(order) * 0.75)]
            tr, te = lab[lab["flight_id"].isin(cut)], lab[~lab["flight_id"].isin(cut)]
            if tr["drop"].nunique() == 2 and len(te) >= 100:
                clf = _drop_clf().fit(tr[DROP_FEATURES], tr["drop"])
                p_clf = clf.predict_proba(te[DROP_FEATURES])[:, 1]
                p_emp = _drop_empirical(_rates(tr), te["h2d"].values)
                p_ana = _analog_drop(SimilarCases(tr).query(te), te["h2d"].values)
                y_te = te["drop"].values
                drop_bt = {"brier_gbm": round(float(np.mean((p_clf - y_te) ** 2)), 4),
                           "brier_empirical": round(float(np.mean((p_emp - y_te) ** 2)), 4),
                           "brier_analog": round(float(np.mean((p_ana - y_te) ** 2)), 4),
                           "base_rate": round(float(te["drop"].mean()), 3), "n": int(len(te))}
                best_d = min(["gbm", "empirical", "analog"], key=lambda k: drop_bt[f"brier_{k}"])
                if best_d == "gbm":
                    drop_method, drop_model = "gbm", _drop_clf().fit(lab[DROP_FEATURES], lab["drop"])
                elif best_d == "analog":
                    drop_method = "analog"

    similar = SimilarCases(lab) if len(lab) >= 100 else None
    return Trained(method, model, backtest, n_pairs, round(data_days, 2),
                   drop_method, drop_model, drop_bt, drop_base, interval_scale=scale, similar=similar)


def _analog_drop(sims: list[dict], h2d) -> np.ndarray:
    prior = _drop_prior(h2d)
    return np.array([(s["n"] * s["pct_drop"] + 10 * pr) / (s["n"] + 10) for s, pr in zip(sims, prior)])


def _drop_clf():
    return HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.05, min_samples_leaf=30, l2_regularization=1.0,
        categorical_features=[DROP_FEATURES.index(c) for c in CAT_FEATURES], random_state=0)


def _rates(lab):
    b = _bin(lab["h2d"].values, H2D_BINS)
    r = pd.DataFrame({"b": b, "y": lab["drop"].values}).groupby("b")["y"].agg(["sum", "count"])
    return {int(k): (float(v["sum"]), float(v["count"])) for k, v in r.iterrows()}


def _drop_prior(h2d):
    return np.where(np.asarray(h2d) > 72, 0.35, 0.2)


def _drop_empirical(rates: dict, h2d) -> np.ndarray:
    h2d = np.asarray(h2d, float)
    prior = _drop_prior(h2d)
    b = _bin(h2d, H2D_BINS)
    out = prior.copy()
    for k in range(len(h2d)):
        s, n = rates.get(int(b[k]), (0.0, 0.0))
        out[k] = (s + SHRINK_K * prior[k]) / (n + SHRINK_K)
    return out


# ===================================================================== forecast
def _prob_below(qrow: np.ndarray, target: float) -> float:
    """P(Y < target) από 3 ποσοστημόρια (γραμμική παρεμβολή, εκθετικές ουρές)."""
    q10, q50, q90 = qrow
    if target <= q10:
        scale = max(q50 - q10, 1e-4)
        return 0.1 * math.exp((target - q10) / scale * 1.28)
    if target <= q50:
        return 0.1 + 0.4 * (target - q10) / max(q50 - q10, 1e-6)
    if target <= q90:
        return 0.5 + 0.4 * (target - q50) / max(q90 - q50, 1e-6)
    scale = max(q90 - q50, 1e-4)
    return 1 - 0.1 * math.exp(-(target - q90) / scale * 1.28)


def forecast(d: pd.DataFrame, tr: Trained, cfg) -> dict:
    """Πρόβλεψη για όλες τις πτήσεις που εμφανίστηκαν στον τελευταίο κύκλο."""
    if d.empty:
        return {}
    latest_t = d["t"].max()
    cur = d[(d["t"] == latest_t) & (d["h2d"] > 0)].copy()
    if cur.empty:
        return {}
    rows = []
    for idx, r in cur.iterrows():
        last_step = int(math.floor((r["h2d"] - 0.5) / STEP_H)) * STEP_H
        for h in range(STEP_H, min(last_step, MAX_H) + 1, STEP_H):
            rows.append({**{f: r[f] for f in FEATURES if f not in ("horizon", "h2d_end")},
                         "horizon": h, "h2d_end": r["h2d"] - h, "_idx": idx})
    out: dict = {}
    grid = pd.DataFrame(rows)
    if not grid.empty:
        q = predict_any(tr.model, grid)
        grid[["q10", "q50", "q90"]] = apply_scale(q, tr.interval_scale)

    # πιθανότητα πτώσης
    if tr.drop_method == "gbm":
        p_drop_all = tr.drop_model.predict_proba(cur[DROP_FEATURES])[:, 1]
    elif tr.drop_method == "analog" and tr.similar is not None:
        p_drop_all = _analog_drop(tr.similar.query(cur), cur["h2d"].values)
    elif tr.drop_method == "empirical":
        p_drop_all = _drop_empirical(tr.drop_base, cur["h2d"].values)
    else:
        p_drop_all = None

    sims = tr.similar.query(cur) if tr.similar is not None else [None] * len(cur)

    for k, (idx, r) in enumerate(cur.iterrows()):
        p0 = float(r["price"])
        thr = max(cfg.drop_threshold_eur, p0 * cfg.drop_threshold_pct)
        steps = []
        g = grid[grid["_idx"] == idx] if not grid.empty else grid
        for _, s in g.iterrows():
            steps.append({
                "t": (latest_t + pd.Timedelta(hours=float(s["horizon"]))).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "h": int(s["horizon"]),
                "q10": round(p0 * math.exp(s["q10"]), 2),
                "q50": round(p0 * math.exp(s["q50"]), 2),
                "q90": round(p0 * math.exp(s["q90"]), 2),
            })
        if p_drop_all is not None:
            p_drop = float(p_drop_all[k])
        elif len(g):
            target = math.log((p0 - thr) / p0)
            p_drop = max(_prob_below(np.array([s["q10"], s["q50"], s["q90"]]), target) for _, s in g.iterrows())
        else:
            p_drop = 0.0
        p_drop = float(np.clip(p_drop, 0.02, 0.98))  # ποτέ «σίγουρα»: τιμές μπορεί πάντα να κινηθούν
        exp_low = min([s["q50"] for s in steps] + [p0])
        trend = (steps[min(3, len(steps) - 1)]["q50"] - p0) if steps else 0.0
        if r["h2d"] < 6:
            rec, why = "buy", "Η πτήση φεύγει σε λιγότερο από 6 ώρες."
        elif p_drop >= 0.6:
            rec, why = "wait", f"Πιθανότητα {p_drop:.0%} να δεις τιμή ≤ {p0 - thr:.0f}€ πριν την πτήση."
        elif p_drop <= 0.3:
            rec = "buy"
            why = (f"Μόνο {p_drop:.0%} πιθανότητα για πτώση ≥ {thr:.0f}€"
                   + ("· το μοντέλο βλέπει άνοδο." if trend > 1 else "."))
        else:
            rec, why = "hold", f"Αμφίρροπο: {p_drop:.0%} πιθανότητα πτώσης ≥ {thr:.0f}€."
        out[r["flight_id"]] = {"steps": steps, "p_drop": round(p_drop, 3), "drop_threshold": round(thr, 2),
                               "expected_low": round(exp_low, 2), "rec": rec, "rec_text": why,
                               "similar": sims[k]}
    return out
