import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["as", "render"],
  _excluded2 = ["as", "render"],
  _excluded3 = ["as", "render"],
  _excluded4 = ["as", "render"],
  _excluded5 = ["as", "render"],
  _excluded6 = ["as", "render"],
  _excluded7 = ["as", "render"],
  _excluded8 = ["as", "render"],
  _excluded9 = ["as", "render"],
  _excluded0 = ["as", "render"],
  _excluded1 = ["as", "render"],
  _excluded10 = ["as", "render"],
  _excluded11 = ["as", "render"];
import { Fragment, useEffect, useState } from 'react';
import { useStore } from "../store.js";
import { useTypesStore } from "../store/Types.js";
import { ValueQuote } from "../symbol/index.js";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export var bigIntToString = bi => {
  if (bi === undefined) {
    return '0n';
  } else if (typeof bi === 'string') {
    try {
      bi = BigInt(bi);
    } catch (e) {
      return '0n';
    }
  }
  return bi ? bi.toString() + 'n' : '0n';
};
export var SetComp = _ref => {
  var value = _ref.value,
    keyName = _ref.keyName;
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Set = _useTypesStore.Set,
    Comp = _useTypesStore$Set === void 0 ? {} : _useTypesStore$Set,
    displayDataTypes = _useTypesStore.displayDataTypes;
  var isSet = value instanceof Set;
  if (!isSet || !displayDataTypes) return null;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded);
  var isRender = render && typeof render === 'function';
  var type = isRender && render(reset, {
    type: 'type',
    value,
    keyName
  });
  if (type) return type;
  var Elm = as || 'span';
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset));
};
SetComp.displayName = 'JVR.SetComp';
export var MapComp = _ref2 => {
  var value = _ref2.value,
    keyName = _ref2.keyName;
  var _useTypesStore2 = useTypesStore(),
    _useTypesStore2$Map = _useTypesStore2.Map,
    Comp = _useTypesStore2$Map === void 0 ? {} : _useTypesStore2$Map,
    displayDataTypes = _useTypesStore2.displayDataTypes;
  var isMap = value instanceof Map;
  if (!isMap || !displayDataTypes) return null;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded2);
  var isRender = render && typeof render === 'function';
  var type = isRender && render(reset, {
    type: 'type',
    value,
    keyName
  });
  if (type) return type;
  var Elm = as || 'span';
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset));
};
MapComp.displayName = 'JVR.MapComp';
var defalutStyle = {
  opacity: 0.75,
  paddingRight: 4
};
export var TypeString = _ref3 => {
  var _ref3$children = _ref3.children,
    children = _ref3$children === void 0 ? '' : _ref3$children,
    keyName = _ref3.keyName,
    keys = _ref3.keys;
  var _useTypesStore3 = useTypesStore(),
    _useTypesStore3$Str = _useTypesStore3.Str,
    Str = _useTypesStore3$Str === void 0 ? {} : _useTypesStore3$Str,
    displayDataTypes = _useTypesStore3.displayDataTypes;
  var _useStore = useStore(),
    _useStore$shortenText = _useStore.shortenTextAfterLength,
    length = _useStore$shortenText === void 0 ? 30 : _useStore$shortenText,
    _useStore$stringEllip = _useStore.stringEllipsis,
    stringEllipsis = _useStore$stringEllip === void 0 ? '...' : _useStore$stringEllip;
  var as = Str.as,
    render = Str.render,
    reset = _objectWithoutPropertiesLoose(Str, _excluded3);
  var childrenStr = children;
  var _useState = useState(length && childrenStr.length > length),
    shorten = _useState[0],
    setShorten = _useState[1];
  useEffect(() => setShorten(length && childrenStr.length > length), [length]);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Str.style || {});
  if (length > 0) {
    reset.style = _extends({}, reset.style, {
      cursor: childrenStr.length <= length ? 'initial' : 'pointer'
    });
    if (childrenStr.length > length) {
      reset.onClick = () => {
        setShorten(!shorten);
      };
    }
  }
  var text = shorten ? "" + childrenStr.slice(0, length) + stringEllipsis : childrenStr;
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var cls = shorten ? 'w-rjv-value w-rjv-value-short' : 'w-rjv-value';
  var child = isRender && render(_extends({}, reset, {
    children: text,
    className: cls
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsxs(Fragment, {
      children: [/*#__PURE__*/_jsx(ValueQuote, {}), /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
        className: cls,
        children: text
      })), /*#__PURE__*/_jsx(ValueQuote, {})]
    })]
  });
};
TypeString.displayName = 'JVR.TypeString';
export var TypeTrue = _ref4 => {
  var children = _ref4.children,
    keyName = _ref4.keyName,
    keys = _ref4.keys;
  var _useTypesStore4 = useTypesStore(),
    _useTypesStore4$True = _useTypesStore4.True,
    True = _useTypesStore4$True === void 0 ? {} : _useTypesStore4$True,
    displayDataTypes = _useTypesStore4.displayDataTypes;
  var as = True.as,
    render = True.render,
    reset = _objectWithoutPropertiesLoose(True, _excluded4);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, True.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: children == null ? void 0 : children.toString()
    }))]
  });
};
TypeTrue.displayName = 'JVR.TypeTrue';
export var TypeFalse = _ref5 => {
  var children = _ref5.children,
    keyName = _ref5.keyName,
    keys = _ref5.keys;
  var _useTypesStore5 = useTypesStore(),
    _useTypesStore5$False = _useTypesStore5.False,
    False = _useTypesStore5$False === void 0 ? {} : _useTypesStore5$False,
    displayDataTypes = _useTypesStore5.displayDataTypes;
  var as = False.as,
    render = False.render,
    reset = _objectWithoutPropertiesLoose(False, _excluded5);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, False.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: children == null ? void 0 : children.toString()
    }))]
  });
};
TypeFalse.displayName = 'JVR.TypeFalse';
export var TypeFloat = _ref6 => {
  var children = _ref6.children,
    keyName = _ref6.keyName,
    keys = _ref6.keys;
  var _useTypesStore6 = useTypesStore(),
    _useTypesStore6$Float = _useTypesStore6.Float,
    Float = _useTypesStore6$Float === void 0 ? {} : _useTypesStore6$Float,
    displayDataTypes = _useTypesStore6.displayDataTypes;
  var as = Float.as,
    render = Float.render,
    reset = _objectWithoutPropertiesLoose(Float, _excluded6);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Float.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: children == null ? void 0 : children.toString()
    }))]
  });
};
TypeFloat.displayName = 'JVR.TypeFloat';
export var TypeInt = _ref7 => {
  var children = _ref7.children,
    keyName = _ref7.keyName,
    keys = _ref7.keys;
  var _useTypesStore7 = useTypesStore(),
    _useTypesStore7$Int = _useTypesStore7.Int,
    Int = _useTypesStore7$Int === void 0 ? {} : _useTypesStore7$Int,
    displayDataTypes = _useTypesStore7.displayDataTypes;
  var as = Int.as,
    render = Int.render,
    reset = _objectWithoutPropertiesLoose(Int, _excluded7);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Int.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: children == null ? void 0 : children.toString()
    }))]
  });
};
TypeInt.displayName = 'JVR.TypeInt';
export var TypeBigint = _ref8 => {
  var children = _ref8.children,
    keyName = _ref8.keyName,
    keys = _ref8.keys;
  var _useTypesStore8 = useTypesStore(),
    _useTypesStore8$Bigin = _useTypesStore8.Bigint,
    CompBigint = _useTypesStore8$Bigin === void 0 ? {} : _useTypesStore8$Bigin,
    displayDataTypes = _useTypesStore8.displayDataTypes;
  var as = CompBigint.as,
    render = CompBigint.render,
    reset = _objectWithoutPropertiesLoose(CompBigint, _excluded8);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, CompBigint.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: bigIntToString(children == null ? void 0 : children.toString())
    }))]
  });
};
TypeBigint.displayName = 'JVR.TypeFloat';
export var TypeUrl = _ref9 => {
  var children = _ref9.children,
    keyName = _ref9.keyName,
    keys = _ref9.keys;
  var _useTypesStore9 = useTypesStore(),
    _useTypesStore9$Url = _useTypesStore9.Url,
    Url = _useTypesStore9$Url === void 0 ? {} : _useTypesStore9$Url,
    displayDataTypes = _useTypesStore9.displayDataTypes;
  var as = Url.as,
    render = Url.render,
    reset = _objectWithoutPropertiesLoose(Url, _excluded9);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Url.style);
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children: children == null ? void 0 : children.href,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsxs("a", _extends({
      href: children == null ? void 0 : children.href,
      target: "_blank"
    }, reset, {
      className: "w-rjv-value",
      children: [/*#__PURE__*/_jsx(ValueQuote, {}), children == null ? void 0 : children.href, /*#__PURE__*/_jsx(ValueQuote, {})]
    }))]
  });
};
TypeUrl.displayName = 'JVR.TypeUrl';
export var TypeDate = _ref0 => {
  var children = _ref0.children,
    keyName = _ref0.keyName,
    keys = _ref0.keys;
  var _useTypesStore0 = useTypesStore(),
    _useTypesStore0$Date = _useTypesStore0.Date,
    CompData = _useTypesStore0$Date === void 0 ? {} : _useTypesStore0$Date,
    displayDataTypes = _useTypesStore0.displayDataTypes;
  var as = CompData.as,
    render = CompData.render,
    reset = _objectWithoutPropertiesLoose(CompData, _excluded0);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, CompData.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var childStr = children instanceof Date ? children.toLocaleString() : children;
  var child = isRender && render(_extends({}, reset, {
    children: childStr,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: childStr
    }))]
  });
};
TypeDate.displayName = 'JVR.TypeDate';
export var TypeUndefined = _ref1 => {
  var children = _ref1.children,
    keyName = _ref1.keyName,
    keys = _ref1.keys;
  var _useTypesStore1 = useTypesStore(),
    _useTypesStore1$Undef = _useTypesStore1.Undefined,
    Undefined = _useTypesStore1$Undef === void 0 ? {} : _useTypesStore1$Undef,
    displayDataTypes = _useTypesStore1.displayDataTypes;
  var as = Undefined.as,
    render = Undefined.render,
    reset = _objectWithoutPropertiesLoose(Undefined, _excluded1);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Undefined.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: "undefined"
    }))]
  });
};
TypeUndefined.displayName = 'JVR.TypeUndefined';
export var TypeNull = _ref10 => {
  var children = _ref10.children,
    keyName = _ref10.keyName,
    keys = _ref10.keys;
  var _useTypesStore10 = useTypesStore(),
    _useTypesStore10$Null = _useTypesStore10.Null,
    Null = _useTypesStore10$Null === void 0 ? {} : _useTypesStore10$Null,
    displayDataTypes = _useTypesStore10.displayDataTypes;
  var as = Null.as,
    render = Null.render,
    reset = _objectWithoutPropertiesLoose(Null, _excluded10);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Null.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children,
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: "null"
    }))]
  });
};
TypeNull.displayName = 'JVR.TypeNull';
export var TypeNan = _ref11 => {
  var children = _ref11.children,
    keyName = _ref11.keyName,
    keys = _ref11.keys;
  var _useTypesStore11 = useTypesStore(),
    _useTypesStore11$Nan = _useTypesStore11.Nan,
    Nan = _useTypesStore11$Nan === void 0 ? {} : _useTypesStore11$Nan,
    displayDataTypes = _useTypesStore11.displayDataTypes;
  var as = Nan.as,
    render = Nan.render,
    reset = _objectWithoutPropertiesLoose(Nan, _excluded11);
  var Comp = as || 'span';
  var style = _extends({}, defalutStyle, Nan.style || {});
  var isRender = render && typeof render === 'function';
  var type = isRender && render(_extends({}, reset, {
    style
  }), {
    type: 'type',
    value: children,
    keyName,
    keys
  });
  var child = isRender && render(_extends({}, reset, {
    children: children == null ? void 0 : children.toString(),
    className: 'w-rjv-value'
  }), {
    type: 'value',
    value: children,
    keyName,
    keys
  });
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [displayDataTypes && (type || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      style: style
    }))), child || /*#__PURE__*/_jsx(Comp, _extends({}, reset, {
      className: "w-rjv-value",
      children: "NaN"
    }))]
  });
};
TypeNan.displayName = 'JVR.TypeNan';