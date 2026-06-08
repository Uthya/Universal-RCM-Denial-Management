"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault")["default"];
Object.defineProperty(exports, "__esModule", {
  value: true
});
var _exportNames = {
  JsonView: true,
  BraceLeft: true,
  BraceRight: true,
  BracketsLeft: true,
  BracketsRight: true,
  Arrow: true,
  Colon: true,
  Quote: true,
  ValueQuote: true,
  Bigint: true,
  JsonDate: true,
  False: true,
  Float: true,
  Int: true,
  JsonMap: true,
  Nan: true,
  Null: true,
  JsonSet: true,
  JsonString: true,
  True: true,
  Undefined: true,
  Url: true,
  Copied: true,
  CountInfo: true,
  CountInfoExtra: true,
  Ellipsis: true,
  KeyName: true,
  Row: true
};
Object.defineProperty(exports, "Arrow", {
  enumerable: true,
  get: function get() {
    return _Arrow.Arrow;
  }
});
Object.defineProperty(exports, "Bigint", {
  enumerable: true,
  get: function get() {
    return _Bigint.Bigint;
  }
});
Object.defineProperty(exports, "BraceLeft", {
  enumerable: true,
  get: function get() {
    return _BraceLeft.BraceLeft;
  }
});
Object.defineProperty(exports, "BraceRight", {
  enumerable: true,
  get: function get() {
    return _BraceRight.BraceRight;
  }
});
Object.defineProperty(exports, "BracketsLeft", {
  enumerable: true,
  get: function get() {
    return _BracketsLeft.BracketsLeft;
  }
});
Object.defineProperty(exports, "BracketsRight", {
  enumerable: true,
  get: function get() {
    return _BracketsRight.BracketsRight;
  }
});
Object.defineProperty(exports, "Colon", {
  enumerable: true,
  get: function get() {
    return _Colon.Colon;
  }
});
Object.defineProperty(exports, "Copied", {
  enumerable: true,
  get: function get() {
    return _Copied.Copied;
  }
});
Object.defineProperty(exports, "CountInfo", {
  enumerable: true,
  get: function get() {
    return _CountInfo.CountInfo;
  }
});
Object.defineProperty(exports, "CountInfoExtra", {
  enumerable: true,
  get: function get() {
    return _CountInfoExtra.CountInfoExtra;
  }
});
Object.defineProperty(exports, "Ellipsis", {
  enumerable: true,
  get: function get() {
    return _Ellipsis.Ellipsis;
  }
});
Object.defineProperty(exports, "False", {
  enumerable: true,
  get: function get() {
    return _False.False;
  }
});
Object.defineProperty(exports, "Float", {
  enumerable: true,
  get: function get() {
    return _Float.Float;
  }
});
Object.defineProperty(exports, "Int", {
  enumerable: true,
  get: function get() {
    return _Int.Int;
  }
});
Object.defineProperty(exports, "JsonDate", {
  enumerable: true,
  get: function get() {
    return _Date.Date;
  }
});
Object.defineProperty(exports, "JsonMap", {
  enumerable: true,
  get: function get() {
    return _Map.Map;
  }
});
Object.defineProperty(exports, "JsonSet", {
  enumerable: true,
  get: function get() {
    return _Set.Set;
  }
});
Object.defineProperty(exports, "JsonString", {
  enumerable: true,
  get: function get() {
    return _String.StringText;
  }
});
exports.JsonView = void 0;
Object.defineProperty(exports, "KeyName", {
  enumerable: true,
  get: function get() {
    return _KeyName.KeyName;
  }
});
Object.defineProperty(exports, "Nan", {
  enumerable: true,
  get: function get() {
    return _Nan.Nan;
  }
});
Object.defineProperty(exports, "Null", {
  enumerable: true,
  get: function get() {
    return _Null.Null;
  }
});
Object.defineProperty(exports, "Quote", {
  enumerable: true,
  get: function get() {
    return _Quote.Quote;
  }
});
Object.defineProperty(exports, "Row", {
  enumerable: true,
  get: function get() {
    return _Row.Row;
  }
});
Object.defineProperty(exports, "True", {
  enumerable: true,
  get: function get() {
    return _True.True;
  }
});
Object.defineProperty(exports, "Undefined", {
  enumerable: true,
  get: function get() {
    return _Undefined.Undefined;
  }
});
Object.defineProperty(exports, "Url", {
  enumerable: true,
  get: function get() {
    return _Url.Url;
  }
});
Object.defineProperty(exports, "ValueQuote", {
  enumerable: true,
  get: function get() {
    return _ValueQuote.ValueQuote;
  }
});
exports["default"] = void 0;
var _objectSpread2 = _interopRequireDefault(require("@babel/runtime/helpers/objectSpread2"));
var _objectWithoutProperties2 = _interopRequireDefault(require("@babel/runtime/helpers/objectWithoutProperties"));
var _react = require("react");
var _store = require("./store");
Object.keys(_store).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _store[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _store[key];
    }
  });
});
var _Container = require("./Container");
var _BraceLeft = require("./symbol/BraceLeft");
var _BraceRight = require("./symbol/BraceRight");
var _BracketsLeft = require("./symbol/BracketsLeft");
var _BracketsRight = require("./symbol/BracketsRight");
var _Arrow = require("./symbol/Arrow");
var _Colon = require("./symbol/Colon");
var _Quote = require("./symbol/Quote");
var _ValueQuote = require("./symbol/ValueQuote");
var _Bigint = require("./types/Bigint");
var _Date = require("./types/Date");
var _False = require("./types/False");
var _Float = require("./types/Float");
var _Int = require("./types/Int");
var _Map = require("./types/Map");
var _Nan = require("./types/Nan");
var _Null = require("./types/Null");
var _Set = require("./types/Set");
var _String = require("./types/String");
var _True = require("./types/True");
var _Undefined = require("./types/Undefined");
var _Url = require("./types/Url");
var _Copied = require("./section/Copied");
var _CountInfo = require("./section/CountInfo");
var _CountInfoExtra = require("./section/CountInfoExtra");
var _Ellipsis = require("./section/Ellipsis");
var _KeyName = require("./section/KeyName");
var _Row = require("./section/Row");
var _jsxRuntime = require("react/jsx-runtime");
var _Expands = require("./store/Expands");
Object.keys(_Expands).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _Expands[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _Expands[key];
    }
  });
});
var _ShowTools = require("./store/ShowTools");
Object.keys(_ShowTools).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _ShowTools[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _ShowTools[key];
    }
  });
});
var _Symbols = require("./store/Symbols");
Object.keys(_Symbols).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _Symbols[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _Symbols[key];
    }
  });
});
var _Types = require("./store/Types");
Object.keys(_Types).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _Types[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _Types[key];
    }
  });
});
var _symbol = require("./symbol/");
Object.keys(_symbol).forEach(function (key) {
  if (key === "default" || key === "__esModule") return;
  if (Object.prototype.hasOwnProperty.call(_exportNames, key)) return;
  if (key in exports && exports[key] === _symbol[key]) return;
  Object.defineProperty(exports, key, {
    enumerable: true,
    get: function get() {
      return _symbol[key];
    }
  });
});
var _excluded = ["className", "style", "value", "children", "collapsed", "shouldExpandNodeInitially", "indentWidth", "displayObjectSize", "shortenTextAfterLength", "stringEllipsis", "highlightUpdates", "enableClipboard", "displayDataTypes", "objectSortKeys", "onExpand", "onCopied", "beforeCopy"];
var JsonView = exports.JsonView = /*#__PURE__*/(0, _react.forwardRef)(function (props, ref) {
  var _props$className = props.className,
    className = _props$className === void 0 ? '' : _props$className,
    style = props.style,
    value = props.value,
    children = props.children,
    _props$collapsed = props.collapsed,
    collapsed = _props$collapsed === void 0 ? false : _props$collapsed,
    shouldExpandNodeInitially = props.shouldExpandNodeInitially,
    _props$indentWidth = props.indentWidth,
    indentWidth = _props$indentWidth === void 0 ? 15 : _props$indentWidth,
    _props$displayObjectS = props.displayObjectSize,
    displayObjectSize = _props$displayObjectS === void 0 ? true : _props$displayObjectS,
    _props$shortenTextAft = props.shortenTextAfterLength,
    shortenTextAfterLength = _props$shortenTextAft === void 0 ? 30 : _props$shortenTextAft,
    stringEllipsis = props.stringEllipsis,
    _props$highlightUpdat = props.highlightUpdates,
    highlightUpdates = _props$highlightUpdat === void 0 ? true : _props$highlightUpdat,
    _props$enableClipboar = props.enableClipboard,
    enableClipboard = _props$enableClipboar === void 0 ? true : _props$enableClipboar,
    _props$displayDataTyp = props.displayDataTypes,
    displayDataTypes = _props$displayDataTyp === void 0 ? true : _props$displayDataTyp,
    _props$objectSortKeys = props.objectSortKeys,
    objectSortKeys = _props$objectSortKeys === void 0 ? false : _props$objectSortKeys,
    onExpand = props.onExpand,
    onCopied = props.onCopied,
    beforeCopy = props.beforeCopy,
    elmProps = (0, _objectWithoutProperties2["default"])(props, _excluded);
  var defaultStyle = (0, _objectSpread2["default"])({
    lineHeight: 1.4,
    fontFamily: 'var(--w-rjv-font-family, Menlo, monospace)',
    color: 'var(--w-rjv-color, #002b36)',
    backgroundColor: 'var(--w-rjv-background-color, #00000000)',
    fontSize: 13
  }, style);
  var cls = ['w-json-view-container', 'w-rjv', className].filter(Boolean).join(' ');
  return /*#__PURE__*/(0, _jsxRuntime.jsxs)(_store.Provider, {
    initialState: {
      value: value,
      objectSortKeys: objectSortKeys,
      indentWidth: indentWidth,
      shouldExpandNodeInitially: collapsed === false ? shouldExpandNodeInitially : undefined,
      displayObjectSize: displayObjectSize,
      collapsed: collapsed,
      enableClipboard: enableClipboard,
      shortenTextAfterLength: shortenTextAfterLength,
      stringEllipsis: stringEllipsis,
      highlightUpdates: highlightUpdates,
      onCopied: onCopied,
      onExpand: onExpand,
      beforeCopy: beforeCopy
    },
    initialTypes: {
      displayDataTypes: displayDataTypes
    },
    children: [/*#__PURE__*/(0, _jsxRuntime.jsx)(_Container.Container, (0, _objectSpread2["default"])((0, _objectSpread2["default"])({
      value: value
    }, elmProps), {}, {
      ref: ref,
      className: cls,
      style: defaultStyle
    })), children]
  });
});
JsonView.Bigint = _Bigint.Bigint;
JsonView.Date = _Date.Date;
JsonView.False = _False.False;
JsonView.Float = _Float.Float;
JsonView.Int = _Int.Int;
JsonView.Map = _Map.Map;
JsonView.Nan = _Nan.Nan;
JsonView.Null = _Null.Null;
JsonView.Set = _Set.Set;
JsonView.String = _String.StringText;
JsonView.True = _True.True;
JsonView.Undefined = _Undefined.Undefined;
JsonView.Url = _Url.Url;
JsonView.ValueQuote = _ValueQuote.ValueQuote;
JsonView.Arrow = _Arrow.Arrow;
JsonView.Colon = _Colon.Colon;
JsonView.Quote = _Quote.Quote;
JsonView.Ellipsis = _Ellipsis.Ellipsis;
JsonView.BraceLeft = _BraceLeft.BraceLeft;
JsonView.BraceRight = _BraceRight.BraceRight;
JsonView.BracketsLeft = _BracketsLeft.BracketsLeft;
JsonView.BracketsRight = _BracketsRight.BracketsRight;
JsonView.Copied = _Copied.Copied;
JsonView.CountInfo = _CountInfo.CountInfo;
JsonView.CountInfoExtra = _CountInfoExtra.CountInfoExtra;
JsonView.KeyName = _KeyName.KeyName;
JsonView.Row = _Row.Row;
JsonView.displayName = 'JVR.JsonView';

// Named exports for better import flexibility

// Named exports for type components

// Named exports for symbol components

// Named exports for section components
var _default = exports["default"] = JsonView;