// Black implied volatility on the normalised price, vectorised: the same algorithm as optmm/bs.py (parity to the
// out-of-the-money side, the call/put symmetry to an OTM call with x <= 0, Halley steps inside a bisection bracket).
// Built with pybind11 into optmm._iv; optmm.iv uses it when present and the numpy version otherwise.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cmath>
#include <limits>

namespace py = pybind11;

static inline double ncdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

static inline double norm_price(double x, double s) {           // OTM call, x <= 0
    double h = x / s, t = 0.5 * s;
    return std::exp(0.5 * x) * ncdf(h + t) - std::exp(-0.5 * x) * ncdf(h - t);
}

static inline double norm_vega(double x, double s) {
    double h = x / s, t = 0.5 * s;
    return std::exp(-0.5 * (h * h + t * t)) / std::sqrt(2.0 * M_PI);
}

static inline double ninv(double p) {                          // Acklam's rational approximation, refined by one Newton step
    static const double a[] = {-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00};
    static const double b[] = {-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01};
    static const double c[] = {-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00};
    static const double d[] = {7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00};
    if (p <= 0.0) return -std::numeric_limits<double>::infinity();
    if (p >= 1.0) return std::numeric_limits<double>::infinity();
    double q, r, x;
    if (p < 0.02425) { q = std::sqrt(-2 * std::log(p)); x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
    else if (p > 1 - 0.02425) { q = std::sqrt(-2 * std::log(1 - p)); x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
    else { q = p - 0.5; r = q * q; x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1); }
    double e = ncdf(x) - p; double u = e * std::sqrt(2 * M_PI) * std::exp(0.5 * x * x); x = x - u / (1 + 0.5 * x * u);
    return x;
}

static double implied_total_vol_1(double beta, double x, double theta, double tol, int max_iter) {
    if (theta * x > 0) { beta -= theta * (std::exp(0.5 * x) - std::exp(-0.5 * x)); theta = -theta; }
    if (theta < 0) x = -x;
    double upper = std::exp(0.5 * x);
    if (!(beta > 0.0) || !(beta < upper) || !std::isfinite(x)) return std::numeric_limits<double>::quiet_NaN();
    double lo = 1e-10, hi = 40.0, s;
    if (std::fabs(x) < 1e-10) s = -2.0 * ninv(std::min(std::max((1.0 - beta) / 2.0, 1e-300), 0.5));
    else s = std::sqrt(2.0 * std::fabs(x)) + 0.5 * -2.0 * ninv(std::min(std::max((1.0 - beta / std::exp(0.5 * x)) / 2.0, 1e-300), 0.5));
    if (!std::isfinite(s)) s = 1.0;
    s = std::min(std::max(s, 1e-6), 30.0);
    for (int it = 0; it < max_iter; ++it) {
        double f = norm_price(x, s) - beta;
        if (std::fabs(f) < tol) break;
        if (f < 0) lo = s; else hi = s;
        double v = norm_vega(x, s), h = x / s;
        double fpp = v * (h * h / s - s / 4.0);
        double newton = f / v;
        double s_new = s - newton / (1.0 - 0.5 * newton * fpp / v);
        if (!std::isfinite(s_new) || s_new <= lo || s_new >= hi) s_new = 0.5 * (lo + hi);
        s = s_new;
    }
    return s;
}

py::array_t<double> implied_total_vol(py::array_t<double, py::array::c_style | py::array::forcecast> beta, py::array_t<double, py::array::c_style | py::array::forcecast> x, py::array_t<double, py::array::c_style | py::array::forcecast> theta, double tol, int max_iter) {
    auto b = beta.unchecked<1>(), xx = x.unchecked<1>(), th = theta.unchecked<1>();
    py::ssize_t n = b.shape(0);
    py::array_t<double> out(n); auto o = out.mutable_unchecked<1>();
    for (py::ssize_t i = 0; i < n; ++i) o(i) = implied_total_vol_1(b(i), xx(i), th(i), tol, max_iter);
    return out;
}

PYBIND11_MODULE(_iv, m) {
    m.doc() = "Black implied total volatility (C++), same algorithm as optmm.bs";
    m.def("implied_total_vol", &implied_total_vol, py::arg("beta"), py::arg("x"), py::arg("theta"), py::arg("tol") = 1e-12, py::arg("max_iter") = 60);
}
