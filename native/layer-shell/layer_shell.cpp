#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <LayerShellQt/window.h>

#include <QGuiApplication>
#include <QMargins>
#include <QSize>
#include <QWidget>

#include <cstdint>

namespace
{

PyObject *qt_version(PyObject *, PyObject *)
{
    return PyUnicode_FromString(QT_VERSION_STR);
}

PyObject *configure_window(PyObject *, PyObject *args)
{
    unsigned long long address = 0;
    int width = 0;
    int height = 0;
    int margin = 0;
    int activateOnShow = 0;
    if (!PyArg_ParseTuple(args, "Kiiii", &address, &width, &height, &margin, &activateOnShow)) {
        return nullptr;
    }
    if (address == 0 || width <= 0 || height <= 0 || margin < 0
        || (activateOnShow != 0 && activateOnShow != 1)) {
        PyErr_SetString(PyExc_ValueError, "invalid widget pointer, size, or margin");
        return nullptr;
    }

    auto *widget = reinterpret_cast<QWidget *>(static_cast<std::uintptr_t>(address));
    widget->winId();
    auto *window = widget->windowHandle();
    if (!window || QGuiApplication::platformName() != QStringLiteral("wayland")) {
        Py_RETURN_FALSE;
    }

    auto *layerWindow = LayerShellQt::Window::get(window);
    if (!layerWindow) {
        Py_RETURN_FALSE;
    }

    layerWindow->setLayer(LayerShellQt::Window::LayerTop);
    LayerShellQt::Window::Anchors anchors;
    anchors |= LayerShellQt::Window::AnchorBottom;
    anchors |= LayerShellQt::Window::AnchorRight;
    layerWindow->setAnchors(anchors);
    layerWindow->setMargins(QMargins(0, margin, margin, 0));
    layerWindow->setExclusiveZone(0);
    layerWindow->setDesiredSize(QSize(width, height));
    layerWindow->setKeyboardInteractivity(
        LayerShellQt::Window::KeyboardInteractivityOnDemand
    );
    layerWindow->setActivateOnShow(activateOnShow != 0);
    Py_RETURN_TRUE;
}

PyMethodDef methods[] = {
    {"qt_version", qt_version, METH_NOARGS, "Return the Qt version used to build this bridge."},
    {"configure_window", configure_window, METH_VARARGS,
     "Configure a QWidget as a bottom-right wlr-layer-shell surface."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_layer_shell",
    "Optional native LayerShellQt bridge for PySide6 windows.",
    -1,
    methods,
};

} // namespace

PyMODINIT_FUNC PyInit__layer_shell()
{
    return PyModule_Create(&module);
}
