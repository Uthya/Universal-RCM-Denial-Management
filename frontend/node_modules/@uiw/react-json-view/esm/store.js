import _extends from "@babel/runtime/helpers/extends";
import React, { createContext, useContext, useEffect, useReducer } from 'react';
import { useShowTools, ShowTools } from "./store/ShowTools.js";
import { useExpands, Expands } from "./store/Expands.js";
import { useTypes, Types } from "./store/Types.js";
import { useSymbols, Symbols } from "./store/Symbols.js";
import { useSection, Section } from "./store/Section.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var initialState = {
  objectSortKeys: false,
  indentWidth: 15
};
export var Context = /*#__PURE__*/createContext(initialState);
Context.displayName = 'JVR.Context';
var DispatchContext = /*#__PURE__*/createContext(() => {});
DispatchContext.displayName = 'JVR.DispatchContext';
export function reducer(state, action) {
  return _extends({}, state, action);
}
export var useStore = () => {
  return useContext(Context);
};
export var useDispatchStore = () => {
  return useContext(DispatchContext);
};
export var Provider = _ref => {
  var children = _ref.children,
    init = _ref.initialState,
    initialTypes = _ref.initialTypes;
  var _useReducer = useReducer(reducer, Object.assign({}, initialState, init)),
    state = _useReducer[0],
    dispatch = _useReducer[1];
  var _useShowTools = useShowTools(),
    showTools = _useShowTools[0],
    showToolsDispatch = _useShowTools[1];
  var _useExpands = useExpands(),
    expands = _useExpands[0],
    expandsDispatch = _useExpands[1];
  var _useTypes = useTypes(),
    types = _useTypes[0],
    typesDispatch = _useTypes[1];
  var _useSymbols = useSymbols(),
    symbols = _useSymbols[0],
    symbolsDispatch = _useSymbols[1];
  var _useSection = useSection(),
    section = _useSection[0],
    sectionDispatch = _useSection[1];
  useEffect(() => dispatch(_extends({}, init)), [init]);
  return /*#__PURE__*/_jsx(Context.Provider, {
    value: state,
    children: /*#__PURE__*/_jsx(DispatchContext.Provider, {
      value: dispatch,
      children: /*#__PURE__*/_jsx(ShowTools, {
        initial: showTools,
        dispatch: showToolsDispatch,
        children: /*#__PURE__*/_jsx(Expands, {
          initial: expands,
          dispatch: expandsDispatch,
          children: /*#__PURE__*/_jsx(Types, {
            initial: _extends({}, types, initialTypes),
            dispatch: typesDispatch,
            children: /*#__PURE__*/_jsx(Symbols, {
              initial: symbols,
              dispatch: symbolsDispatch,
              children: /*#__PURE__*/_jsx(Section, {
                initial: section,
                dispatch: sectionDispatch,
                children: children
              })
            })
          })
        })
      })
    })
  });
};
export function useDispatch() {
  return useContext(DispatchContext);
}
Provider.displayName = 'JVR.Provider';